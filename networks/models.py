from torch import nn
import torch
import torch.nn.functional as F
from networks.pn2_utils import *


# -------------------------------------Encoder-----------------------------------
class PCN_encoder(nn.Module):
    def __init__(self, input_num):
        super(PCN_encoder, self).__init__()
        self.i_num = input_num
        self.conv1 = torch.nn.Conv1d(3, 128, 1)  # [in_channels, out_channels, kernel_size]
        self.conv2 = torch.nn.Conv1d(128, 256, 1)
        self.conv3 = torch.nn.Conv1d(512, 512, 1)
        self.conv4 = torch.nn.Conv1d(512, 512, 1)

    def forward(self, x):
        """
         input x is a batch of partial point clouds whose shapes are
          [batch_size, 3(channels), partial_point_cloud_number]
        """
        x = F.relu(self.conv1(x))
        x = self.conv2(x)
        global_feature_1 = torch.max(x, dim=2, keepdim=True)[0].repeat(1, 1, self.i_num)
        x = torch.cat([x, global_feature_1], dim=1)
        x = F.relu(self.conv3(x))
        x = self.conv4(x)
        global_feature_2 = torch.max(x, dim=2, keepdim=True)[0]  # [batch_size, 1024, 1]
        return global_feature_2


class PN2_Transformer_Encoder(nn.Module):
    def __init__(self, out_dim=512):
        """Encoder that encodes information of partial point cloud"""
        super().__init__()
        # self.sa_module_1 = PointNet_SA_Module_KNN(512, 16, 3, [64, 128], group_all=False, if_bn=False, if_idx=True)
        # self.transformer_1 = Transformer(128, dim=64)
        # self.sa_module_2 = PointNet_SA_Module_KNN(128, 16, 64, [128, 256], group_all=False, if_bn=False, if_idx=True)
        # self.transformer_2 = Transformer(256, dim=64)
        # self.sa_module_3 = PointNet_SA_Module_KNN(None, None, 256, [512, out_dim], group_all=True, if_bn=False)

        self.sa_module_1 = PointNet_SA_Module_KNN(256, 16, 3, [32, 64], group_all=False, if_bn=False, if_idx=True)
        self.transformer_1 = Transformer(64, dim=32)
        self.sa_module_2 = PointNet_SA_Module_KNN(64, 16, 64, [64, 128], group_all=False, if_bn=False, if_idx=True)
        self.transformer_2 = Transformer(128, dim=32)
        self.sa_module_3 = PointNet_SA_Module_KNN(None, None, 128, [256, out_dim], group_all=True, if_bn=False)

    def forward(self, point_cloud):
        """
        Args:
        point_cloud: b, 3, n

        Returns:
        l3_points: (B, out_dim, 1)
        """
        l0_xyz = point_cloud
        l0_points = point_cloud

        l1_xyz, l1_points, idx1 = self.sa_module_1(l0_xyz, l0_points)  # (B, 3, 256), (B, 64, 256)
        l1_points = self.transformer_1(l1_points, l1_xyz)  # (B, 64, 256)
        l2_xyz, l2_points, idx2 = self.sa_module_2(l1_xyz, l1_points)  # (B, 3, 128), (B, 128, 128)
        l2_points = self.transformer_2(l2_points, l2_xyz)
        l3_xyz, l3_points = self.sa_module_3(l2_xyz, l2_points)  # (B, 3, 1), (B, out_dim, 1)

        return l3_points


class Feature_transfrom(nn.Module):
    def __init__(self):
        super(Feature_transfrom, self).__init__()
        self.conv1 = torch.nn.Conv1d(1536, 1024, 1)  # [in_channels, out_channels, kernel_size]
        self.conv2 = torch.nn.Conv1d(1024, 512, 1)
        self.conv3 = torch.nn.Conv1d(2048, 1024, 1)
        self.conv4 = torch.nn.Conv1d(1024, 512, 1)

    def forward(self, feature):
        """
         input feature is a batch of feature whose shapes are
          [batch_size, dim_input, 1]
         after the network, the shapes are
          [batch, dim_output, 1]
        """
        x = F.relu(self.conv1(feature))
        x = F.relu(self.conv2(x))
        x = torch.cat([x, feature], dim=1)
        x = F.relu(self.conv3(x))
        global_feature = self.conv4(x)
        return global_feature


# -------------------------------------Decoder-----------------------------------
class mlp(nn.Module):
    def __init__(self, in_num, out_num):
        super(mlp, self).__init__()
        self.conv1 = torch.nn.Conv1d(in_num, 256, 1)
        self.conv2 = torch.nn.Conv1d(256, 64, 1)
        self.conv3 = torch.nn.Conv1d(64, out_num, 1)

    def forward(self, x):
        x = F.relu(self.conv1(x))
        x = F.relu(self.conv2(x))
        x = F.relu(self.conv3(x))
        return x


class final_mlp(nn.Module):
    def __init__(self, in_num, out_num):
        super(final_mlp, self).__init__()
        self.conv1 = torch.nn.Conv1d(in_num, 256, 1)
        self.conv2 = torch.nn.Conv1d(256, 64, 1)
        self.conv3 = torch.nn.Conv1d(64, out_num, 1)

    def forward(self, x):
        x = F.relu(self.conv1(x))
        x = F.relu(self.conv2(x))
        x = self.conv3(x)
        return x


class TopNet_decoder(nn.Module):
    def __init__(self, arch=[4, 4, 8, 8]):
        super(TopNet_decoder, self).__init__()
        self.arch = arch
        self.level_num = len(arch)
        self.level_1 = nn.ModuleList()
        self.level_2 = nn.ModuleList()
        self.level_3 = nn.ModuleList()
        self.level_4 = nn.ModuleList()
        # construct tree
        for _ in range(arch[0]):
            self.level_1.append(mlp(512, 8))
        for _ in range(arch[1]):
            self.level_2.append(mlp(512 + 8, 8))
        for _ in range(arch[2]):
            self.level_3.append(mlp(512 + 8, 8))
        for _ in range(arch[3]):
            self.level_4.append(final_mlp(512 + 8, 3))

    def forward(self, x):
        features_1 = []
        for net in self.level_1:
            features_1.append(torch.cat([net(x), x], dim=1))
        features_2 = []
        for feat in features_1:
            for net in self.level_2:
                features_2.append(torch.cat([net(feat), x], dim=1))
        features_3 = []
        for feat in features_2:
            for net in self.level_3:
                features_3.append(torch.cat([net(feat), x], dim=1))
        features_4 = []
        for feat in features_3:
            for net in self.level_4:
                features_4.append(net(feat))
        pc = torch.cat(features_4, dim=2)
        return pc


# -------------------------------------Completion Net-----------------------------------

# class IBPCDCNet_ibs(nn.Module):
#     def __init__(self, pcd_point_num, IBS_point_num):
#         super().__init__()
#         # encoder
#         self.encoder_pcd = PCN_encoder(pcd_point_num)
#         self.encoder_IBS = PCN_encoder(IBS_point_num)
#         # feature transform
#         self.feature_transform = Feature_transfrom()
#         # decoder
#         self.decoder = TopNet_decoder()

#     def forward(self, pcd_partial, IBS):
#         # exchange dim1 and dim2
#         pcd_partial = pcd_partial.permute(0, 2, 1)
#         IBS = IBS.permute(0, 2, 1)

#         # get the feature of pcd1, pcd2, IBS
#         feature_pcd = self.encoder_pcd(pcd_partial)
#         feature_IBS = self.encoder_IBS(IBS)

#         # concat the global feature
#         feature_global = torch.cat((feature_pcd, feature_IBS), dim=1)

#         # transform feature
#         feature_global = self.feature_transform(feature_global)

#         # decode
#         pcd_out = self.decoder(feature_global).permute(0, 2, 1)

#         return pcd_out


class IBPCDCNet(nn.Module):
    def __init__(self):
        super().__init__()
        self.encoder_pcd1 = PN2_Transformer_Encoder()
        self.encoder_pcd2 = PN2_Transformer_Encoder()
        self.decoder_pcd1 = TopNet_decoder()
        self.decoder_pcd2 = TopNet_decoder()

    def forward(self, pcd1_partial, pcd2_partial, IBS):
        # (B, n, 3) -> (B, 3, n)
        pcd1_partial = pcd1_partial.permute(0, 2, 1)
        pcd2_partial = pcd2_partial.permute(0, 2, 1)
        # IBS = IBS.permute(0, 2, 1)

        # 先各自进行特征提取，(B, 3, n) -> (B, feature_dim, 1)
        feature_pcd1 = self.encoder_pcd1(pcd1_partial)
        feature_pcd2 = self.encoder_pcd2(pcd2_partial)
        # feature_IBS = self.encoder_IBS(IBS)

        # 获取总体特征，(B, 3*feature_dim, 1) -> (B, feature_dim, 1)
        feature = torch.cat([feature_pcd1, feature_pcd2], 1)
        # feature = self.feature_transform(feature)

        # 与各自特征进行拼接，(B, 2*feature_dim, 1)
        # feature_pcd1 = torch.cat([feature_pcd1, feature], 1)
        # feature_pcd2 = torch.cat([feature_pcd2, feature], 1)

        # (B, 2*feature_dim, 1) -> (B, points_num, 3)
        pcd1_out = self.decoder_pcd1(feature).permute(0, 2, 1)
        pcd2_out = self.decoder_pcd2(feature).permute(0, 2, 1)

        return pcd1_out, pcd2_out
