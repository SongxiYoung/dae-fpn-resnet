import cv2
import numpy as np
import matplotlib.pyplot as plt
import logging
import torch
import os
import shutil
from PIL import Image
from torchvision.utils import make_grid
import torch.nn.functional as F

# from osgeo import gdal


# def read_geotiff(geotiff_path):
#     img = gdal.Open(geotiff_path)
#     if not img:
#         print('image open failed: {}'.format(geotiff_path))
#         return None
#     height, width, bands = img.RasterYSize, img.RasterXSize, img.RasterCount
#     img_np = np.zeros((height, width, bands), dtype=np.float32)
#     for b in range(bands):
#         img_np[:, :, b] = img.GetRasterBand(b+1).ReadAsArray()
#     return np.squeeze(img_np) # in case nBand == 1


class AverageMeter(object):
    """Computes and stores the average and current value"""

    def __init__(self):
        self.reset()

    def reset(self):
        self.val = 0.0
        self.avg = 0.0
        self.sum = 0
        self.count = 0.0

    def update(self, val, n=1):
        self.val = val
        self.sum += val * n
        self.count += n
        self.avg = self.sum / self.count


def vis_ms(img_ms, r, g, b):
    """

    :param img_ms: tensor images [B, C, H, W]
    :param r: int
    :param g: int
    :param b: int
    :return:
    """
    # extract rgb bands from multispectral image
    img_ms_subspec = torch.cat(
        (
            img_ms[:, r].unsqueeze(1),
            img_ms[:, g].unsqueeze(1),
            img_ms[:, b].unsqueeze(1),
        ),
        dim=1,
    )
    return img_ms_subspec

def vis_single_channel(img, channel):
    """
    :param img: tensor images [B, C, H, W]
    :param channel: int
    :return:
    """
    # extract the specified channel from the image
    img_subspec = img[:, channel].unsqueeze(1)
     # apply threshold to create a binary map
    img_subspec = (img_subspec > 0.5).float()

    return img_subspec

def vis_single_channel_multiclasses(img, channel):
    """
    :param img: tensor images [B, C, H, W]
    :param channel: int
    :return: colorized image [B, 3, H, W]
    """
    device = img.device
    batch_size, channels, height, width = img.shape

    colors = {
        0: torch.tensor([0, 0, 0, 1]).long().to(device),  # 黑色
        1: torch.tensor([0, 1, 0, 1]).long().to(device),  # 绿色
        2: torch.tensor([0, 0, 1, 1]).long().to(device),  # 蓝色
        3: torch.tensor([1, 1, 0, 1]).long().to(device),  # 黄色
        4: torch.tensor([1, 0, 0, 1]).long().to(device),  # 红色
    }
    img_color = torch.zeros((batch_size, height, width, 4)).long().to(device)


    for class_idx, color in colors.items():
        img_color = torch.where(img.float().permute(0, 2, 3, 1) == class_idx, color, img_color)

    img_color = img_color.permute(0, 3, 1, 2).float()

    return img_color

def overall_accuracy_pytorch(y_pred, y_true):
    """
    Overall accuracy for multi-class prediction
    :param y_pred: (tensor) [batch, n_class]
    :param y_true: (tensor) [batch, ]
    :return: acc
    """
    assert isinstance(y_pred, torch.Tensor)
    assert isinstance(y_true, torch.Tensor)

    y_pred_classified = torch.argmax(y_pred, dim=1)
    n_sample = y_true.numel()
    acc = (y_pred_classified == y_true).sum().float() / n_sample

    return acc


def precision_recall_multi_class_pytorch(y_pred, y_true):
    """
    this is for batch-wise evaluation, not for the entire dataset
    :param y_pred: (tensor) (batch, n_class)
    :param y_true: (tensor) (batch)
    :return: precision, recall, f1 for each class
    """
    assert isinstance(y_pred, torch.Tensor)
    assert isinstance(y_true, torch.Tensor)

    n_class = y_pred.shape[1]
    y_pred_classified = torch.argmax(y_pred, dim=1)

    precision = torch.zeros(n_class, dtype=torch.float)
    recall = torch.zeros(n_class, dtype=torch.float)
    f1 = torch.zeros(n_class, dtype=torch.float)

    eps = 1e-6

    for i in range(n_class):
        y_pred_i = y_pred_classified == i
        y_true_i = y_true == i

        TP = (y_pred_i & y_true_i).sum().float()  # true positive
        FP = y_pred_i.sum().float() - TP  # false positive
        FN = y_true_i.sum().float() - TP  # false negative
        # TN = (y_pred_i == y_true_i).sum().float() - TP

        precision[i] = (TP + eps) / (TP + FP + eps)
        recall[i] = (TP + eps) / (TP + FN + eps)
        f1[i] = 2.0 / (1.0 / precision[i] + 1.0 / recall[i])

    return precision, recall, f1


def precision_recall_binary_pytorch(y_pred, y_true):
    """
    this is for batch-wise evaluation, not for the entire dataset
    :param y_pred: (tensor) 1D
    :param y_true: (tensor) 1D
    :return: precision, recall, f1, accuracy
    """
    assert isinstance(y_pred, torch.Tensor)
    assert isinstance(y_true, torch.Tensor)
    assert y_pred.shape == y_true.shape

    y_pred = y_pred > 0.5
    y_true = y_true.to(dtype=torch.bool)

    n_sample = y_true.numel()
    acc = (y_pred == y_true).sum().float() / n_sample

    TP = (y_pred & y_true).sum().float()  # true positive
    FP = y_pred.sum().float() - TP  # false positive
    FN = y_true.sum().float() - TP  # false negative
    # TN = (y_pred == y_true).sum().float() - TP

    eps = 1e-6
    precision = (TP + eps) / (TP + FP + eps)
    recall = (TP + eps) / (TP + FN + eps)
    f1 = 2.0 * precision * recall / (precision + recall)
    # accuracy = (TP + TN) / (TP + TN + FP + FN)

    return precision, recall, f1, acc


def iou_pytorch(y_pred, y_true):
    """
    IoU of the outputs
    :param y_pred: (tensor) [B, 1, H, W]
    :param y_true: (tensor) [B, 1, H, W]
    :return: IoU score
    """
    # BATCH x 1 x H x W => BATCH x H x W
    y_pred = y_pred.squeeze(dim=1)
    y_true = y_true.squeeze(dim=1)
    intersection = (
        (y_pred & y_true).sum((1, 2)).float()
    )  # Will be zero if Truth=0 or Prediction=0,
    union = (y_pred | y_true).sum((1, 2)).float()  # Will be zzero if both are 0

    eps = 1e-6
    iou = (intersection + eps) / (union + eps)  # We smooth our devision to avoid 0/0

    # thresholded = torch.clamp(20 * (iou - 0.5), 0, 10).ceil() / 10  # This is equal to comparing with thresolds

    return (
        iou.mean()
    )  # Or thresholded.mean() if you are interested in average across the batch


def NT_Xent_Loss_v3(z_pre, z_post, tau=0.1):
    """
    Contrastive loss: InfoNCE loss
    :param z_pre: (tensor) [batch, channels]
    :param z_post: (tensor) [batch, channels]
    :param tau: temperature
    :return: InfoNCE loss
    """
    assert isinstance(z_pre, torch.Tensor)
    assert isinstance(z_post, torch.Tensor)
    assert z_pre.shape == z_post.shape

    device = z_post.device
    loss = torch.tensor(0.0, device=device)

    # similarity between pairs of pre+post representations
    n_samples, n_features = z_post.shape  # Batch size, Feature dimensions

    # pairs similarity
    for i in range(n_samples):
        sim_positive = (
            F.cosine_similarity(z_pre[i], z_post[i], dim=0) / tau
        ).exp()  # positive pairs similarity

        curr_pre_batch = torch.cat([z_pre[[i]]] * (n_samples - 1), dim=0)
        curr_post_batch = torch.cat([z_post[[i]]] * (n_samples - 1), dim=0)
        others_pre_batch = torch.cat([z_pre[:i], z_pre[i + 1 :]], dim=0)
        others_post_batch = torch.cat([z_post[:i], z_post[i + 1 :]], dim=0)

        sim_negative = (
            (F.cosine_similarity(curr_pre_batch, others_pre_batch, dim=1) / tau)
            .exp()
            .sum()
            + (F.cosine_similarity(curr_pre_batch, others_post_batch, dim=1) / tau)
            .exp()
            .sum()
            + (F.cosine_similarity(curr_post_batch, others_pre_batch, dim=1) / tau)
            .exp()
            .sum()
            + (F.cosine_similarity(curr_post_batch, others_post_batch, dim=1) / tau)
            .exp()
            .sum()
            + sim_positive
        )

        loss = loss - torch.log(sim_positive / sim_negative)
    loss = loss / n_samples
    return loss


def NT_Xent_Loss_v4(z_pre, z_post, tau=0.1):
    """
    Contrastive loss: InfoNCE loss - Normalized temperature-scaled cross entropy loss
    :param z_pre: (tensor) [batch, channels]
    :param z_post: (tensor) [batch, channels]
    :param tau: temperature
    :return: InfoNCE loss
    """
    assert isinstance(z_pre, torch.Tensor)
    assert isinstance(z_post, torch.Tensor)
    assert z_pre.shape == z_post.shape

    device = z_post.device
    loss = torch.tensor(0.0, device=device)

    # similarity between pairs of pre+post representations
    n_samples, _ = z_post.shape  # Batch size, Feature dimensions

    # pairs similarity
    for i in range(n_samples):
        sim_pos_i = (
            torch.dot(z_pre[i], z_post[i]).div(tau).exp()
        )  # positive pairs similarity
        # pre as anchor
        sim_neg_pre = (
            torch.matmul(
                torch.cat((z_pre[:i], z_pre[i + 1 :], z_post), dim=0), z_pre[i]
            )
            .div(tau)
            .exp()
            .sum()
        )

        # post as anchor
        sim_neg_post = (
            torch.matmul(
                torch.cat((z_post[:i], z_post[i + 1 :], z_pre), dim=0), z_post[i]
            )
            .div(tau)
            .exp()
            .sum()
        )

        loss_pre = -torch.log(sim_pos_i / sim_neg_pre)
        loss_post = -torch.log(sim_pos_i / sim_neg_post)

        loss = loss + loss_pre + loss_post
    loss = loss / (2 * n_samples)
    return loss


def barlow_twins_loss(z_pre, z_post, lmd=5e-3):
    """Loss function: computing the cross-correlation (cc) matrix  of two representations,
    if they are variants of the same image, the cc matrix should be close to the identity matrix.
    Ref: Zbontar et al 2021. Barlow Twins: Self-Supervised Learning via Redundancy Reduction. ICML 2021
    Args:
        z_pre (tensor) [n_samples, n_features]: image representations of pre-image
        z_post (tensor) [n_samples, n_features]: image representations of post-image
    Return:
        correlation loss
    """
    n_samples, n_features = z_post.shape
    device = z_post.device

    # normalize representations, along the batch dimension
    z_pre_norm = (z_pre - z_pre.mean(0)) / z_pre.std(0)
    z_post_norm = (z_post - z_post.mean(0)) / z_post.std(0)

    # cross-correlation matrix, [D, D]
    cc = torch.matmul(z_pre_norm.T, z_post_norm) / n_samples

    # loss
    identity = torch.eye(n_features, device=device)
    cc_diff = (cc - identity) ** 2
    # multiple off-diagonal elems of cc_diff by lmd
    cc_diff = cc_diff * identity + lmd * cc_diff * (1.0 - identity)
    return cc_diff.sum()

def batch_similarity(pre, recon):
    """
    :param post: (tensor) [batch, channels, H, W]
    :param recon: (tensor) [batch, channels, H, W]
    :return: similarity
    """
    assert isinstance(pre, torch.Tensor)
    assert isinstance(recon, torch.Tensor)
    assert pre.shape == recon.shape

    n_samples, n_channels, H, W = pre.shape
    sim = torch.zeros(n_samples, device=pre.device)

    for i in range(n_samples):
        sim[i] = F.cosine_similarity(
            pre[i].view(1, -1), recon[i].view(1, -1), dim=1
        )

    return sim  # return the tensor of similarities

def compute_gradient_similarity(pre, recon):
    assert isinstance(pre, torch.Tensor)
    assert isinstance(recon, torch.Tensor)
    assert pre.shape == recon.shape

    n_samples, n_channels, H, W = pre.shape
    grad_sim = torch.zeros(n_samples, device=pre.device)

    for i in range(n_samples):
        # Gradient
        grad_x1 = cv2.Sobel(pre[i].cpu().numpy(), cv2.CV_64F, 1, 0, ksize=3)
        grad_y1 = cv2.Sobel(pre[i].cpu().numpy(), cv2.CV_64F, 0, 1, ksize=3)
        grad_x2 = cv2.Sobel(recon[i].cpu().numpy(), cv2.CV_64F, 1, 0, ksize=3)
        grad_y2 = cv2.Sobel(recon[i].cpu().numpy(), cv2.CV_64F, 0, 1, ksize=3)

        # Delta
        grad1 = np.sqrt(grad_x1**2 + grad_y1**2)
        grad2 = np.sqrt(grad_x2**2 + grad_y2**2)

        # Compute similarity
        grad_sim[i] = F.cosine_similarity(
            torch.from_numpy(grad1).view(1, -1), torch.from_numpy(grad2).view(1, -1), dim=1
        )

    return grad_sim  # return the tensor of gradient similarities

def harmonic_mean(values):
    return len(values) / sum(1.0 / val for val in values)

def calculate_iou(pred, target, threshold=0.5):
    pred = (pred > threshold).float()
    intersection = (pred * target).sum()
    union = pred.sum() + target.sum() - intersection
    if union == 0:
        return float('nan')  # 如果union为0，返回NaN
    else:
        return intersection / union

def mean_iou(preds, targets, threshold=0.5):
    ious = []
    for pred, target in zip(preds, targets):
        iou = calculate_iou(pred, target, threshold)
        if not torch.isnan(iou):
            ious.append(iou)
    if len(ious) == 0:
        return float('nan')
    return sum(ious) / len(ious)

def show_tensor_img(img, str_title=""):
    """
    show tensor image
    :param img: (tensor) [C, H, W]
    :return:
    """
    plt.figure()
    npimg = img.detach().cpu().numpy()
    npimg = np.transpose(npimg, (1, 2, 0))
    npimg = (npimg * 255).astype(np.uint8)
    pil_img = Image.fromarray(npimg)
    pil_img.save("./img_" + str_title + ".png")
    plt.close()
    # plt.imshow(np.transpose(npimg, (1, 2, 0)), interpolation="nearest")
    # plt.show()

def save_tensor_img(img, str_title="", save_path=None):
    """
    show tensor image
    :param img: (tensor) [C, H, W]
    :return:
    """
    plt.figure()
    npimg = img.detach().cpu().numpy()
    npimg = np.transpose(npimg, (1, 2, 0))
    npimg = (npimg * 255).astype(np.uint8)
    pil_img = Image.fromarray(npimg)
    pil_img.save(save_path + "/" + str_title + ".png")
    plt.close()
    # plt.imshow(np.transpose(npimg, (1, 2, 0)), interpolation="nearest")
    # plt.show()

def set_logger(log_path):
    """Set the logger to log info in terminal and file `log_path`.
    In general, it is useful to have a logger so that every output to the terminal is saved
    in a permanent file. Here we save it to `model_dir/train.log`.
    Example:
    ```
    logging.info("Starting training...")
    ```
    Args:
        log_path: (string) where to log
    """
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    if logger.handlers:
        for hdlr in logger.handlers[:]:  # remove all old handlers
            logger.removeHandler(hdlr)

    if not logger.handlers:
        # Logging to a file
        file_handler = logging.FileHandler(log_path)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s:%(levelname)s: %(message)s")
        )
        logger.addHandler(file_handler)

        # Logging to console
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(stream_handler)


def save_checkpoint(
    state, is_best, root_dir, checkpoint_name="checkpoint", best_model_name="model_best"
):
    filename_chk = os.path.join(root_dir, "{}.pth.tar".format(checkpoint_name))
    torch.save(state, filename_chk)
    if is_best:
        filename_modelbest = os.path.join(
            root_dir, "{}.pth.tar".format(best_model_name)
        )
        shutil.copyfile(filename_chk, filename_modelbest)
