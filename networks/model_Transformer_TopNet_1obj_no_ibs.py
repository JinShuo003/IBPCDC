import torch.nn.functional as F

from networks.pn2_utils import *


# -------------------------------------Encoder-----------------------------------
class Transformer_Encoder(nn.Module):
    def __init__(self, out_dim=512):
        """Encoder that encodes information of partial point cloud"""
        super().__init__()
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
    def __init__(self, arch=[4, 8, 8, 8]):
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
class IBPCDCNet(nn.Module):
    def __init__(self):
        super().__init__()
        self.encoder_pcd = Transformer_Encoder()

        self.decoder_pcd = TopNet_decoder()

    def forward(self, pcd_partial):
        # (B, n, 3) -> (B, 3, n)
        pcd_partial = pcd_partial.permute(0, 2, 1)

        # 特征提取，(B, 3, n) -> (B, feature_dim, 1)
        feature_pcd = self.encoder_pcd(pcd_partial)

        # (B, feature_dim, 1) -> (B, points_num, 3)
        pcd_out = self.decoder_pcd(feature_pcd).permute(0, 2, 1)

        return pcd_out
