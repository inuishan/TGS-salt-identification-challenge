import pandas as pd
import os
import torch
import torch.nn as nn
import torch.nn.functional as F
from torchvision import models
import torchvision
import cv2
from pathlib import Path
from torch.nn import functional as F

directory = './input'


def conv3x3(in_, out):
    return nn.Conv2d(in_, out, 3, padding=1)


class ConvRelu(nn.Module):
    def __init__(self, in_, out):
        super().__init__()
        self.conv = conv3x3(in_, out)
        self.activation = nn.ReLU(inplace=True)

    def forward(self, x):
        x = self.conv(x)
        x = self.activation(x)
        return x


class DecoderBlock(nn.Module):
    def __init__(self, in_channels, middle_channels, out_channels):
        super().__init__()

        self.block = nn.Sequential(
            ConvRelu(in_channels, middle_channels),
            nn.ConvTranspose2d(middle_channels, out_channels, kernel_size=3, stride=2, padding=1, output_padding=1),
            nn.ReLU(inplace=True)
        )

    def forward(self, x):
        return self.block(x)


class UNet11(nn.Module):
    def __init__(self, num_filters=32):
        """
        :param num_classes:
        :param num_filters:
        """
        super().__init__()
        self.pool = nn.MaxPool2d(2, 2)

        # Convolutions are from VGG11
        self.encoder = models.vgg11().features

        # "relu" layer is taken from VGG probably for generality, but it's not clear
        self.relu = self.encoder[1]

        self.conv1 = self.encoder[0]
        self.conv2 = self.encoder[3]
        self.conv3s = self.encoder[6]
        self.conv3 = self.encoder[8]
        self.conv4s = self.encoder[11]
        self.conv4 = self.encoder[13]
        self.conv5s = self.encoder[16]
        self.conv5 = self.encoder[18]

        self.center = DecoderBlock(num_filters * 8 * 2, num_filters * 8 * 2, num_filters * 8)
        self.dec5 = DecoderBlock(num_filters * (16 + 8), num_filters * 8 * 2, num_filters * 8)
        self.dec4 = DecoderBlock(num_filters * (16 + 8), num_filters * 8 * 2, num_filters * 4)
        self.dec3 = DecoderBlock(num_filters * (8 + 4), num_filters * 4 * 2, num_filters * 2)
        self.dec2 = DecoderBlock(num_filters * (4 + 2), num_filters * 2 * 2, num_filters)
        self.dec1 = ConvRelu(num_filters * (2 + 1), num_filters)

        self.final = nn.Conv2d(num_filters, 1, kernel_size=1, )

    def forward(self, x):
        conv1 = self.relu(self.conv1(x))
        conv2 = self.relu(self.conv2(self.pool(conv1)))
        conv3s = self.relu(self.conv3s(self.pool(conv2)))
        conv3 = self.relu(self.conv3(conv3s))
        conv4s = self.relu(self.conv4s(self.pool(conv3)))
        conv4 = self.relu(self.conv4(conv4s))
        conv5s = self.relu(self.conv5s(self.pool(conv4)))
        conv5 = self.relu(self.conv5(conv5s))

        center = self.center(self.pool(conv5))

        # Deconvolutions with copies of VGG11 layers of corresponding size
        dec5 = self.dec5(torch.cat([center, conv5], 1))
        dec4 = self.dec4(torch.cat([dec5, conv4], 1))
        dec3 = self.dec3(torch.cat([dec4, conv3], 1))
        dec2 = self.dec2(torch.cat([dec3, conv2], 1))
        dec1 = self.dec1(torch.cat([dec2, conv1], 1))
        return F.sigmoid(self.final(dec1))


def unet11(**kwargs):
    model = UNet11(**kwargs)

    return model

device = "cuda"
def get_model():
    model = unet11()
    model.train()
    return model.to(device)


def load_image(path, mask = False):
    """
    Load image from a given path and pad it on the sides, so that eash side is divisible by 32 (newtwork requirement)

    if pad = True:
        returns image as numpy.array, tuple with padding in pixels as(x_min_pad, y_min_pad, x_max_pad, y_max_pad)
    else:
        returns image as numpy.array
    """
    img = cv2.imread(str(path))
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

    height, width, _ = img.shape

    # Padding in needed for UNet models because they need image size to be divisible by 32
    if height % 32 == 0:
        y_min_pad = 0
        y_max_pad = 0
    else:
        y_pad = 32 - height % 32
        y_min_pad = int(y_pad / 2)
        y_max_pad = y_pad - y_min_pad

    if width % 32 == 0:
        x_min_pad = 0
        x_max_pad = 0
    else:
        x_pad = 32 - width % 32
        x_min_pad = int(x_pad / 2)
        x_max_pad = x_pad - x_min_pad

    img = cv2.copyMakeBorder(img, y_min_pad, y_max_pad, x_min_pad, x_max_pad, cv2.BORDER_REFLECT_101)
    if mask:
        # Convert mask to 0 and 1 format
        img = img[:, :, 0:1] // 255
        return torch.from_numpy(img).float().permute([2, 0, 1])
    else:
        img = img / 255.0
        return torch.from_numpy(img).float().permute([2, 0, 1])


import os
import numpy as np
import matplotlib.pyplot as plt
import pandas as pd
import torch

from torch.utils import data

class TGSSaltDataset(data.Dataset):
    def __init__(self, root_path, file_list, is_test = False):
        self.is_test = is_test
        self.root_path = root_path
        self.file_list = file_list

    def __len__(self):
        return len(self.file_list)

    def __getitem__(self, index):
        if index not in range(0, len(self.file_list)):
            return self.__getitem__(np.random.randint(0, self.__len__()))

        file_id = self.file_list[index]

        image_folder = os.path.join(self.root_path, "images")
        image_path = os.path.join(image_folder, file_id + ".png")

        mask_folder = os.path.join(self.root_path, "masks")
        mask_path = os.path.join(mask_folder, file_id + ".png")

        image = load_image(image_path)

        if self.is_test:
            return (image,)
        else:
            mask = load_image(mask_path, mask = True)
            return image, mask

depths_df = pd.read_csv(os.path.join(directory, 'train.csv'))

train_path = os.path.join(directory, 'train')
file_list = list(depths_df['id'].values)

import tqdm

file_list_val = file_list[::10]
file_list_train = [f for f in file_list if f not in file_list_val]
dataset = TGSSaltDataset(train_path, file_list_train)
dataset_val = TGSSaltDataset(train_path, file_list_val)

model = get_model()
#

learning_rate = 1e-4
loss_fn = torch.nn.BCELoss()
optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
for e in range(1):
    train_loss = []
    for image, mask in tqdm.tqdm(data.DataLoader(dataset, batch_size = 30, shuffle = True)):
        image = image.type(torch.float).to(device)
        y_pred = model(image)
        loss = loss_fn(y_pred, mask.to(device))

        optimizer.zero_grad()
        loss.backward()

        optimizer.step()
        train_loss.append(loss.item())

    val_loss = []
    for image, mask in data.DataLoader(dataset_val, batch_size = 50, shuffle = False):
        image = image.to(device)
        y_pred = model(image)

        loss = loss_fn(y_pred, mask.to(device))
        val_loss.append(loss.item())

    print("Epoch: %d, Train: %.3f, Val: %.3f" % (e, np.mean(train_loss), np.mean(val_loss)))


import glob

test_path = os.path.join(directory, 'test')
test_file_list = glob.glob(os.path.join(test_path, 'images', '*.png'))
test_file_list = [f.split('/')[-1].split('.')[0] for f in test_file_list]
test_file_list[:3], test_path

print(len(test_file_list))
test_dataset = TGSSaltDataset(test_path, test_file_list, is_test = True)

all_predictions = []
for image in tqdm.tqdm(data.DataLoader(test_dataset, batch_size = 30)):
    image = image[0].type(torch.float).to(device)
    y_pred = model(image).cpu().detach().numpy()
    all_predictions.append(y_pred)
all_predictions_stacked = np.vstack(all_predictions)[:, 0, :, :]


height, width = 101, 101

if height % 32 == 0:
    y_min_pad = 0
    y_max_pad = 0
else:
    y_pad = 32 - height % 32
    y_min_pad = int(y_pad / 2)
    y_max_pad = y_pad - y_min_pad

if width % 32 == 0:
    x_min_pad = 0
    x_max_pad = 0
else:
    x_pad = 32 - width % 32
    x_min_pad = int(x_pad / 2)
    x_max_pad = x_pad - x_min_pad

all_predictions_stacked = all_predictions_stacked[:, y_min_pad:128 - y_max_pad, x_min_pad:128 - x_max_pad]

test_dataset = TGSSaltDataset(test_path, test_file_list, is_test = True)

val_predictions = []
val_masks = []
for image, mask in tqdm.tqdm(data.DataLoader(dataset_val, batch_size = 30)):
    image = image.type(torch.float).to(device)
    y_pred = model(image).cpu().detach().numpy()
    val_predictions.append(y_pred)
    val_masks.append(mask)

val_predictions_stacked = np.vstack(val_predictions)[:, 0, :, :]

val_masks_stacked = np.vstack(val_masks)[:, 0, :, :]
val_predictions_stacked = val_predictions_stacked[:, y_min_pad:128 - y_max_pad, x_min_pad:128 - x_max_pad]

val_masks_stacked = val_masks_stacked[:, y_min_pad:128 - y_max_pad, x_min_pad:128 - x_max_pad]
val_masks_stacked.shape, val_predictions_stacked.shape

from sklearn.metrics import jaccard_similarity_score

metric_by_threshold = []
for threshold in np.linspace(0, 1, 11):
    val_binary_prediction = (val_predictions_stacked > threshold).astype(int)

    iou_values = []
    for y_mask, p_mask in zip(val_masks_stacked, val_binary_prediction):
        iou = jaccard_similarity_score(y_mask.flatten(), p_mask.flatten())
        iou_values.append(iou)
    iou_values = np.array(iou_values)

    accuracies = [
        np.mean(iou_values > iou_threshold)
        for iou_threshold in np.linspace(0.5, 0.95, 10)
    ]
    print('Threshold: %.1f, Metric: %.3f' % (threshold, np.mean(accuracies)))
    metric_by_threshold.append((np.mean(accuracies), threshold))

best_metric, best_threshold = max(metric_by_threshold)


threshold = best_threshold
binary_prediction = (all_predictions_stacked > threshold).astype(int)

def rle_encoding(x):
    dots = np.where(x.T.flatten() == 1)[0]
    run_lengths = []
    prev = -2
    for b in dots:
        if (b > prev+1): run_lengths.extend((b + 1, 0))
        run_lengths[-1] += 1
        prev = b
    return run_lengths

all_masks = []
for p_mask in list(binary_prediction):
    p_mask = rle_encoding(p_mask)
    all_masks.append(' '.join(map(str, p_mask)))

submit = pd.DataFrame([test_file_list, all_masks]).T
submit.columns = ['id', 'rle_mask']
submit.to_csv('submit_baseline2.csv.gz', compression = 'gzip', index = False)

