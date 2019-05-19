import argparse
import copy

import numpy as np
import torch
from torch import nn, optim
from torch.utils.data import DataLoader

from data_utils import load_data
from models.model_utils import save_checkpoint
from norms.measures import calculate
from models import vgg

save_epochs = [5, 10, 50, 100, 500, 1000]

# train the model for one epoch on the given set
def train(args, model, device, train_loader, criterion, optimizer, epoch, random_labels=False):
    sum_loss, sum_correct = 0, 0

    # switch to train mode
    model.train()

    for i, (data, target) in enumerate(train_loader):
        data, target = data.to(device), target.to(device)

        if random_labels:
            target = target[torch.randperm(target.size()[0])]

        # compute the output
        output = model(data)

        # compute the classification error and loss
        loss = criterion(output, target)
        pred = output.max(1)[1]
        sum_correct += pred.eq(target).sum().item()
        sum_loss += len(data) * loss.item()

        # compute the gradient and do an SGD step
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
    return 1 - (sum_correct / len(train_loader.dataset)), sum_loss / len(train_loader.dataset)


# evaluate the model on the given set
def validate(args, model, device, val_loader, criterion):
    sum_loss, sum_correct = 0, 0
    margin = torch.Tensor([]).to(device)

    # switch to evaluation mode
    model.eval()
    with torch.no_grad():
        for i, (data, target) in enumerate(val_loader):
            data, target = data.to(device), target.to(device)

            # compute the output
            output = model(data)

            # compute the classification error and loss
            pred = output.max(1)[1]
            sum_correct += pred.eq(target).sum().item()
            sum_loss += len(data) * criterion(output, target).item()

            # compute the margin
            output_m = output.clone()
            for i in range(target.size(0)):
                output_m[i, target[i]] = output_m[i,:].min()
            margin = torch.cat((margin, output[:, target].diag() - output_m[:, output_m.max(1)[1]].diag()), 0)
        val_margin = np.percentile( margin.cpu().numpy(), 5 )

    return 1 - (sum_correct / len(val_loader.dataset)), sum_loss / len(val_loader.dataset), val_margin


# This function trains a neural net on the given dataset and calculates various measures on the learned network.
def main():

    # settings
    parser = argparse.ArgumentParser(description='Training a VGG Net')
    # arguments needed for experiments
    parser.add_argument('--randomlabels', default=False, type=bool,
                        help='training with random labels Yes or No? (options: True | False, default: False)')
    parser.add_argument('--numhiddenlayers', default=1, type=int,
                        help='number of hidden layers (options: 1-8k)')
    parser.add_argument('--trainingsetsize', default=-1, type=int,
                        help='size of the training set (options: 1k - 50k')
    parser.add_argument('--model', default='vgg', type=str,
                        help='architecture (options: fc | vgg, default: vgg)')

    # additional arguments
    parser.add_argument('--epochs', default=600, type=int,
                        help='number of epochs to train (default: 1000)')
    parser.add_argument('--stopcond', default=0.01, type=float,
                        help='stopping condtion based on the cross-entropy loss (default: 0.01)')
    parser.add_argument('--no-cuda', default=False, action='store_true',
                        help='disables CUDA training')
    parser.add_argument('--datadir', default='../datasets', type=str,
                        help='path to the directory that contains the datasets (default: datasets)')
    parser.add_argument('--dataset', default='CIFAR10', type=str,
                        help='name of the dataset (options: MNIST | CIFAR10 | CIFAR100 | SVHN, default: CIFAR10)')

    args = parser.parse_args()

    # fixed parameters
    batchsize = 64
    learningrate = 0.01
    momentum = 0.9

    # cuda settings
    use_cuda = not args.no_cuda and torch.cuda.is_available()
    device = torch.device("cuda" if use_cuda else "cpu")
    print("cuda available:", torch.cuda.is_available())
    kwargs = {'num_workers': 1, 'pin_memory': True} if use_cuda else {}

    nchannels, nclasses, img_dim,  = 3, 10, 32
    if args.dataset == 'MNIST': nchannels = 1

    # create an initial model
    #model = getattr(importlib.import_module('.models.{}'.format(args.model)), 'Network')(nchannels, nclasses)
    if args.model == "vgg":
        model = vgg.Network(nchannels, nclasses)
    else:
        print("no valid model input.")
        return
    model = model.to(device)

    # create a copy of the initial model to be used later
    init_model = copy.deepcopy(model)

    # define loss function (criterion) and optimizer
    criterion = nn.CrossEntropyLoss().to(device)
    optimizer = optim.SGD(model.parameters(), learningrate, momentum=momentum)

    # loading data
    train_dataset = load_data('train', args.dataset, args.datadir, nchannels)
    val_dataset = load_data('val', args.dataset, args.datadir, nchannels)

    print("trainings set size: ", args.trainingsetsize)

    if args.trainingsetsize == -1:
        num_samples = len(train_dataset)
    # random seed with restricted size
    sampler = torch.utils.data.RandomSampler(train_dataset, replacement=True, num_samples=num_samples)

    train_loader = DataLoader(train_dataset, batch_size=batchsize, shuffle=False, sampler=sampler, **kwargs)
    val_loader = DataLoader(val_dataset, batch_size=batchsize, shuffle=False, **kwargs)

    # training the model
    for epoch in range(0, args.epochs):
        # train for one epoch
        tr_err, tr_loss = train(args, model, device, train_loader, criterion, optimizer, epoch, random_labels=args.randomlabels)

        val_err, val_loss, val_margin = validate(args, model, device, val_loader, criterion)

        print(f'Epoch: {epoch + 1}/{args.epochs}\t Training loss: {tr_loss:.3f}\t',
                f'Training error: {tr_err:.3f}\t Validation error: {val_err:.3f}')

        if epoch in save_epochs:
            save_checkpoint(epoch, model, optimizer, args.randomlabels, tr_loss, tr_err, val_err, "../saved_models")

        # stop training if the cross-entropy loss is less than the stopping condition
        if tr_loss < args.stopcond: break

    # calculate the training error and margin of the learned model
    tr_err, tr_loss, tr_margin = validate(args, model, device, train_loader, criterion)
    print(f'\nFinal: Training loss: {tr_loss:.3f}\t Training margin {tr_margin:.3f}\t ',
            f'Training error: {tr_err:.3f}\t Validation error: {val_err:.3f}\n')

    # calcualtes various measures and bounds on the learned network
    measure_dict, bound_dict = calculate(model, init_model, device, train_loader, tr_margin, nchannels, nclasses, img_dim)

    print('\n###### Measures')
    for key, value in measure_dict.items():
        print(f'{key.ljust(25):s}:{float(value):3.3}')

    print('\n###### Generalization Bounds')
    for key, value in bound_dict.items():
        print(f'{key.ljust(45):s}:{float(value):3.3}')

if __name__ == '__main__':
    main()
