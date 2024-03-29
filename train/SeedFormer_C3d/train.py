import sys
import os

sys.path.insert(0, os.path.abspath("."))

import os.path

os.environ['CUDA_VISIBLE_DEVICES'] = "1"

from datetime import datetime, timedelta
import argparse
import time
from torch.optim.lr_scheduler import StepLR, _LRScheduler

from models.SeedFormer import SeedFormer
from models.pn2_utils import fps_subsample
from utils import path_utils
from utils.loss import cd_loss_L1, cd_loss_L1_single, emd_loss
from utils.train_utils import *
from dataset import dataset_C3d


class GradualWarmupScheduler(_LRScheduler):
    """ Gradually warm-up(increasing) learning rate in optimizer.
    Proposed in 'Accurate, Large Minibatch SGD: Training ImageNet in 1 Hour'.

    Args:
        optimizer (Optimizer): Wrapped optimizer.
        multiplier: target learning rate = base lr * multiplier if multiplier > 1.0.
            if multiplier = 1.0, lr starts from 0 and ends up with the base_lr.
        total_epoch: target learning rate is reached at total_epoch, gradually
        after_scheduler: after target_epoch, use this scheduler(eg. ReduceLROnPlateau)
    """

    def __init__(self,
                 optimizer,
                 multiplier,
                 total_epoch,
                 after_scheduler=None):
        self.multiplier = multiplier
        if self.multiplier < 1.:
            raise ValueError(
                'multiplier should be greater thant or equal to 1.')
        self.total_epoch = total_epoch
        self.after_scheduler = after_scheduler
        self.finished = False
        super(GradualWarmupScheduler, self).__init__(optimizer)

    def get_lr(self):
        if self.last_epoch > self.total_epoch:
            if self.after_scheduler:
                if not self.finished:
                    self.after_scheduler.base_lrs = [
                        base_lr * self.multiplier for base_lr in self.base_lrs
                    ]
                    self.finished = True
                return self.after_scheduler.get_last_lr()
            return [base_lr * self.multiplier for base_lr in self.base_lrs]

        if self.multiplier == 1.0:
            return [
                base_lr * (float(self.last_epoch) / self.total_epoch)
                for base_lr in self.base_lrs
            ]
        else:
            return [
                base_lr *
                ((self.multiplier - 1.) * self.last_epoch / self.total_epoch +
                 1.) for base_lr in self.base_lrs
            ]

    def step(self, epoch=None, metrics=None):
        if self.finished and self.after_scheduler:
            if epoch is None:
                self.after_scheduler.step(None)
            else:
                self.after_scheduler.step(epoch - self.total_epoch)
            self._last_lr = self.after_scheduler.get_last_lr()
        else:
            return super(GradualWarmupScheduler, self).step(epoch)


def get_optimizer(model):
    optimizer = torch.optim.Adam(filter(lambda p: p.requires_grad, model.parameters()),
                                 lr=0.001,
                                 weight_decay=0,
                                 betas=(.9, .999))
    return optimizer


def get_lr_scheduler(optimizer):
    scheduler_steplr = StepLR(optimizer, step_size=1, gamma=0.1 ** (1 / 100))
    lr_scheduler = GradualWarmupScheduler(optimizer, multiplier=1, total_epoch=20,
                                          after_scheduler=scheduler_steplr)
    return lr_scheduler


def train(network, train_dataloader, lr_schedule, optimizer, epoch, specs, tensorboard_writer):
    logger = LogFactory.get_logger(specs.get("LogOptions"))
    device = specs.get("Device")

    network.train()
    logger.info("")
    logger.info('epoch: {}, learning rate: {}'.format(epoch, optimizer.param_groups[0]["lr"]))

    train_total_loss_dense = 0
    train_total_loss_sub_dense = 0
    train_total_loss_coarse = 0

    for data, idx in train_dataloader:
        pcd_partial, pcd_gt = data
        optimizer.zero_grad()

        pcd_partial = pcd_partial.to(device)
        pcds_pred = network(pcd_partial)

        pcd_gt = pcd_gt.to(device)

        Pc, P1, P2, P3 = pcds_pred

        pcd_gt_2 = fps_subsample(pcd_gt, P2.shape[1])
        pcd_gt_1 = fps_subsample(pcd_gt_2, P1.shape[1])
        pcd_gt_c = fps_subsample(pcd_gt_1, Pc.shape[1])

        cdc = cd_loss_L1(Pc, pcd_gt_c)
        cd1 = cd_loss_L1(P1, pcd_gt_1)
        cd2 = cd_loss_L1(P2, pcd_gt_2)
        cd3 = cd_loss_L1(P3, pcd_gt)

        partial_matching = cd_loss_L1_single(pcd_partial, P3)

        loss_total = cdc + cd1 + cd2 + cd3 + partial_matching

        train_total_loss_dense += cd3.item()
        train_total_loss_sub_dense += cd2.item()
        train_total_loss_coarse += cdc.item()

        loss_total.backward()
        optimizer.step()

    lr_schedule.step()

    record_loss_info(specs, "train_loss_dense", train_total_loss_dense / train_dataloader.__len__(), epoch,
                     tensorboard_writer)
    record_loss_info(specs, "train_loss_sub_dense", train_total_loss_sub_dense / train_dataloader.__len__(), epoch,
                     tensorboard_writer)
    record_loss_info(specs, "train_loss_coarse", train_total_loss_coarse / train_dataloader.__len__(), epoch,
                     tensorboard_writer)


def test(network, test_dataloader, lr_schedule, optimizer, epoch, specs, tensorboard_writer, best_cd, best_epoch):
    logger = LogFactory.get_logger(specs.get("LogOptions"))
    device = specs.get("Device")

    network.eval()
    with torch.no_grad():
        test_total_dense = 0
        test_total_sub_dense = 0
        test_total_coarse = 0
        test_total_emd = 0
        for data, idx in test_dataloader:
            pcd_partial, pcd_gt = data
            pcd_partial = pcd_partial.to(device)

            pcds_pred = network(pcd_partial)

            pcd_gt = pcd_gt.to(device)

            Pc, P1, P2, P3 = pcds_pred

            pcd_gt_2 = fps_subsample(pcd_gt, P2.shape[1])
            pcd_gt_1 = fps_subsample(pcd_gt_2, P1.shape[1])
            pcd_gt_c = fps_subsample(pcd_gt_1, Pc.shape[1])

            cdc = cd_loss_L1(Pc, pcd_gt_c)
            cd1 = cd_loss_L1(P1, pcd_gt_1)
            cd2 = cd_loss_L1(P2, pcd_gt_2)
            cd3 = cd_loss_L1(P3, pcd_gt)

            partial_matching = cd_loss_L1_single(pcd_partial, P3)

            loss_emd = emd_loss(P3, pcd_gt)

            test_total_dense += cd3.item()
            test_total_sub_dense += cd2.item()
            test_total_coarse += cdc.item()
            test_total_emd += loss_emd.item()

        test_avrg_dense = test_total_dense / test_dataloader.__len__()
        record_loss_info(specs, "test_loss_dense", test_total_dense / test_dataloader.__len__(), epoch,
                         tensorboard_writer)
        record_loss_info(specs, "test_loss_sub_dense", test_total_sub_dense / test_dataloader.__len__(), epoch,
                         tensorboard_writer)
        record_loss_info(specs, "test_loss_coarse", test_total_coarse / test_dataloader.__len__(), epoch,
                         tensorboard_writer)
        record_loss_info(specs, "test_loss_emd", test_total_emd / test_dataloader.__len__(), epoch, tensorboard_writer)

        if test_avrg_dense < best_cd:
            best_epoch = epoch
            best_cd = test_avrg_dense
            logger.info('current best epoch: {}, cd: {}'.format(best_epoch, best_cd))
        save_model(specs, network, lr_schedule, optimizer, epoch)

        return best_cd, best_epoch


def main_function(specs):
    logger = LogFactory.get_logger(specs.get("LogOptions"))
    epoch_num = specs.get("TrainOptions").get("NumEpochs")
    continue_train = specs.get("TrainOptions").get("ContinueTrain")

    TIMESTAMP = "{0:%Y-%m-%d_%H-%M-%S/}".format(datetime.now() + timedelta(hours=8))

    logger.info("current network TAG: {}".format(specs.get("TAG")))
    logger.info("current time: {}".format(TIMESTAMP))
    logger.info("There are {} epochs in total".format(epoch_num))

    train_loader, test_loader = get_dataloader(dataset_C3d.C3dDataset, specs)
    checkpoint = None
    network = get_network(specs, SeedFormer, checkpoint)
    optimizer = get_optimizer(network)
    lr_scheduler = get_lr_scheduler(optimizer)
    tensorboard_writer = get_tensorboard_writer(specs)

    best_cd = 1e8
    best_epoch = -1
    epoch_begin = 0
    if continue_train:
        last_epoch = specs.get("TrainOptions").get("ContinueFromEpoch")
        epoch_begin = last_epoch + 1
        logger.info("continue train from epoch {}".format(epoch_begin))
    for epoch in range(epoch_begin, epoch_num + 1):
        time_begin_train = time.time()
        train(network, train_loader, lr_scheduler, optimizer, epoch, specs, tensorboard_writer)
        time_end_train = time.time()
        logger.info("use {} to train".format(time_end_train - time_begin_train))

        time_begin_test = time.time()
        best_cd, best_epoch = test(network, test_loader, lr_scheduler, optimizer, epoch, specs, tensorboard_writer,
                                   best_cd, best_epoch)
        time_end_test = time.time()
        logger.info("use {} to test".format(time_end_test - time_begin_test))

    tensorboard_writer.close()


if __name__ == '__main__':
    arg_parser = argparse.ArgumentParser(description="Train IBPCDC")
    arg_parser.add_argument(
        "--experiment",
        "-e",
        dest="experiment_config_file",
        default="configs/C3d/train/specs_train_SeedFormer_C3d.json",
        required=False,
        help="The experiment config file."
    )

    args = arg_parser.parse_args()

    specs = path_utils.read_config(args.experiment_config_file)

    logger = LogFactory.get_logger(specs.get("LogOptions"))
    logger.info("specs file path: {}".format(args.experiment_config_file))
    logger.info("specs file: \n{}".format(json.dumps(specs, sort_keys=False, indent=4)))

    main_function(specs)
