import MinkowskiEngine as ME
import MinkowskiEngine.MinkowskiFunctional as MF
from util.data_processing import DataProcess as DP

class PruningLayer(ME.MinkowskiNetwork):
    def __init__(self, in_channels, D, alpha):
        super(PruningLayer, self).__init__(D)
        self.alpha = alpha
        self.likelihood_conv = ME.MinkowskiConvolution(in_channels, out_channels=1, kernel_size=1, stride=1, dimension=D)
        self.pruning = ME.MinkowskiPruning()

    def forward(self, x):
        likelihood_map = MF.sigmoid(self.likelihood_conv(x))
        mask = (likelihood_map.F >= self.alpha).squeeze()
        pruned_features = self.pruning(x, mask)
        return likelihood_map, pruned_features
    
class PruningLayer_(ME.MinkowskiNetwork):
    def __init__(self, D, alpha=1):
        super(PruningLayer_, self).__init__(D)
        self.alpha = alpha
        self.pruning = ME.MinkowskiPruning()

    def forward(self, x):
        coords = x.C
        mask = (coords[:,-1] <= self.alpha).squeeze()
        x = self.pruning(x, mask)
        return x

class Conv(ME.MinkowskiNetwork):
    def __init__(self, in_channels, out_channels, D):
        super(Conv, self).__init__(D)
        self.conv = ME.MinkowskiConvolution(in_channels, out_channels, kernel_size=3, stride=1, dimension=D)
        self.norm = ME.MinkowskiBatchNorm(out_channels)

    def forward(self, x):
        x = self.conv(x)
        x = self.norm(x)
        x = MF.relu(x)
        return x

class DownConv(ME.MinkowskiNetwork):
    def __init__(self, in_channels, out_channels, D):
        super(DownConv, self).__init__(D)
        self.conv = ME.MinkowskiConvolution(in_channels, out_channels, kernel_size=3, stride=(2,2,2,1), dimension=D)
        self.norm = ME.MinkowskiBatchNorm(out_channels)

    def forward(self, x):
        x = self.conv(x)
        x = self.norm(x)
        x = MF.relu(x)
        return x
    
class UpConv(ME.MinkowskiNetwork):
    def __init__(self, in_channels, out_channels, D):
        super(UpConv, self).__init__(D)
        self.up_conv = ME.MinkowskiGenerativeConvolutionTranspose(in_channels, out_channels, kernel_size=2, stride=(2,2,2,1), dimension=D)
        self.norm = ME.MinkowskiBatchNorm(out_channels)
        self.pruning = PruningLayer_(D)

    def forward(self, x):
        x = self.up_conv(x)
        x = self.pruning(x)
        x = self.norm(x)
        x = MF.relu(x)
        return x

class FinalConv(ME.MinkowskiNetwork):
    def __init__(self, in_channels, out_channels, D):
        super(FinalConv, self).__init__(D)
        self.conv = ME.MinkowskiConvolution(in_channels, out_channels, kernel_size=3, stride=(1,1,1,2), dimension=D)

    def forward(self, x):
        x = self.conv(x)
        return x

class EncoderBox(ME.MinkowskiNetwork):
    def __init__(self, in_channels, out_channels, D):
        super(EncoderBox, self).__init__(D)
        self.conv = Conv(in_channels, out_channels, D)
        self.down_conv = DownConv(out_channels, out_channels, D)

    def forward(self, x):
        x = self.conv(x)
        x_down = self.down_conv(x)
        return x, x_down

class Bridge(ME.MinkowskiNetwork):
    def __init__(self, in_channels, out_channels, D):
        super(Bridge, self).__init__(D)
        self.conv = Conv(in_channels, out_channels, D)
        self.up_conv = UpConv(out_channels, out_channels, D)

    def forward(self, x):
        x = self.conv(x)
        x = self.up_conv(x)
        return x

class DecoderBox(ME.MinkowskiNetwork):
    def __init__(self, in_channels, out_channels, D, alpha, stride):
        super(DecoderBox, self).__init__(D)
        self.conv = Conv(in_channels, out_channels, D)
        self.pruning = PruningLayer(out_channels, D, alpha)
        self.up_conv = UpConv(out_channels, out_channels, D)
        self.stride = stride

    def forward(self, x, skip_connection):
        x = DP.concatenate_sparse_tensors(x, skip_connection, self.stride)
        x = self.conv(x)
        lh, x = self.pruning(x)
        x = self.up_conv(x)
        return lh, x

class FinalDecoderBox(ME.MinkowskiNetwork):
    def __init__(self, in_channels, mid_channels, out_channels, D, alpha, stride):
        super(FinalDecoderBox, self).__init__(D)
        self.conv1 = Conv(in_channels, mid_channels, D)
        self.pruning = PruningLayer(mid_channels, D, alpha)
        self.conv2 = FinalConv(mid_channels, out_channels, D)
        self.stride = stride

    def forward(self, x, skip_connection):
        x = DP.concatenate_sparse_tensors(x, skip_connection, self.stride)
        x = self.conv1(x)
        lh, x = self.pruning(x)
        x = self.conv2(x)
        return lh, x

class Net1(ME.MinkowskiNetwork):
    def __init__(self, in_channels, out_channels, D, alpha):
        super(Net1, self).__init__(D)
        self.ch = [2, 4, 8, 16, 32]
        self.enc1 = EncoderBox(in_channels, in_channels * self.ch[0], D) 
        self.enc2 = EncoderBox(in_channels * self.ch[0], in_channels * self.ch[1], D)
        self.enc3 = EncoderBox(in_channels * self.ch[1], in_channels * self.ch[2], D)
        self.enc4 = EncoderBox(in_channels * self.ch[2], in_channels * self.ch[3], D)

        self.bridge = Bridge(in_channels * self.ch[3], in_channels * self.ch[4], D)

        self.dec4 = DecoderBox(in_channels * self.ch[4] + in_channels * self.ch[3], in_channels * self.ch[3], D, alpha, (8,8,8,1))
        self.dec3 = DecoderBox(in_channels * self.ch[3] + in_channels * self.ch[2], in_channels * self.ch[2], D, alpha, (4,4,4,1))
        self.dec2 = DecoderBox(in_channels * self.ch[2] + in_channels * self.ch[1], in_channels * self.ch[1], D, alpha, (2,2,2,1))
        self.dec1 = FinalDecoderBox(in_channels * self.ch[1] + in_channels * self.ch[0], in_channels * self.ch[0], out_channels, D, alpha, 1)

    def forward(self, x):
        DP.check_sparse_tensor_shape(x)
        skip1, x = self.enc1(x) #[1,1,1,1], [2,2,2,1]
        DP.check_sparse_tensor_shape(x)
        skip2, x = self.enc2(x) #[2,2,2,1], [4,4,4,1]
        DP.check_sparse_tensor_shape(x)
        skip3, x = self.enc3(x) #[4,4,4,1], [8,8,8,1]
        DP.check_sparse_tensor_shape(x)
        skip4, x = self.enc4(x) #[8,8,8,1], [16,16,16,1]
        DP.check_sparse_tensor_shape(x)

        x = self.bridge(x) #[8,8,8,1]
        DP.check_sparse_tensor_shape(x)

        lh1, x = self.dec4(x, skip4) #[4,4,4,1]
        DP.check_sparse_tensor_shape(x)
        lh2, x = self.dec3(x, skip3) #[2,2,2,1]
        DP.check_sparse_tensor_shape(x)
        lh3, x = self.dec2(x, skip2) #[1,1,1,1]
        DP.check_sparse_tensor_shape(x)
        lh4, x = self.dec1(x, skip1) #[1,1,1,2]
        DP.check_sparse_tensor_shape(x)

        return [lh1, lh2, lh3, lh4], x
