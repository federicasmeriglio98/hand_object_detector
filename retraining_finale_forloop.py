#SCRIPT FOR RE-TRAINING THE NETWORK ¨

# --------------------------------------------------------
# Tensorflow Faster R-CNN
# Licensed under The MIT License [see LICENSE for details]
# Written by Jiasen Lu, Jianwei Yang, based on code from Ross Girshick
# --------------------------------------------------------

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import pprint
pp = pprint.PrettyPrinter(indent=4)
import shutil
import _init_paths
import os
import sys
import numpy as np
import argparse
import pprint
import pdb
import time
import torch
from torch.autograd import Variable
import torch.nn as nn
import torch.optim as optim
import gc
import itertools 
import csv
import torchvision.transforms as transforms
from torch.utils.data.sampler import Sampler

from roi_data_layer.roidb import combined_roidb
from roi_data_layer.roibatchLoader import roibatchLoader
from model.utils.config import cfg, cfg_from_file, cfg_from_list, get_output_dir
from model.utils.net_utils import weights_normal_init, save_net, load_net, \
      adjust_learning_rate, save_checkpoint, clip_gradient

from model.faster_rcnn.vgg16 import vgg16
from model.faster_rcnn.resnet import resnet
#from torchsummary import summary
from numpy import asarray
from numpy import savetxt
try:
    xrange          # Python 2
except NameError:
    xrange = range  # Python 3


def parse_args():
  """
  Parse input arguments
  """
  parser = argparse.ArgumentParser(description='Train a Fast R-CNN network')
  parser.add_argument('--dataset', dest='dataset',
                      help='training dataset',
                      default='pascal_voc', type=str)
  parser.add_argument('--net', dest='net',
                    help='vgg16, res101',
                    default='vgg16', type=str)
  parser.add_argument('--start_epoch', dest='start_epoch',
                      help='starting epoch',
                      default=1, type=int)
  parser.add_argument('--epochs', dest='max_epochs',
                      help='number of epochs to train',
                      default=20, type=int)
  parser.add_argument('--disp_interval', dest='disp_interval',
                      help='number of iterations to display',
                      default=100, type=int)
  parser.add_argument('--checkpoint_interval', dest='checkpoint_interval',
                      help='number of iterations to display',
                      default=10000, type=int)

  parser.add_argument('--save_dir', dest='save_dir',
                      help='directory to save models', default="models",
                      type=str)
  parser.add_argument('--load_dir', dest='load_dir',
                      help='directory to load models',
                      default="models")
  parser.add_argument('--nw', dest='num_workers',
                      help='number of worker to load data',
                      default=0, type=int)
  parser.add_argument('--cuda', dest='cuda',
                      help='whether use CUDA',
                      action='store_true')
  parser.add_argument('--ls', dest='large_scale',
                      help='whether use large imag scale',
                      action='store_true')                      
  parser.add_argument('--mGPUs', dest='mGPUs',
                      help='whether use multiple GPUs',
                      action='store_true')
  parser.add_argument('--bs', dest='batch_size',
                      help='batch_size',
                      default=1, type=int)
  parser.add_argument('--cag', dest='class_agnostic',
                      help='whether perform class_agnostic bbox regression',
                      action='store_true')

# config optimization
  parser.add_argument('--o', dest='optimizer',
                      help='training optimizer',
                      default="adam", type=str)
  parser.add_argument('--lr', dest='lr',
                      help='starting learning rate',
                      default=0.00001, type=float)
  parser.add_argument('--lr_decay_step', dest='lr_decay_step',
                      help='step to do learning rate decay, unit is epoch',
                      default=2, type=int)
  parser.add_argument('--lr_decay_gamma', dest='lr_decay_gamma',
                      help='learning rate decay ratio',
                      default=0.1, type=float)

# set training session
  parser.add_argument('--s', dest='session',
                      help='training session',
                      default=1, type=int)

# resume trained model
  parser.add_argument('--r', dest='resume',
                      help='resume checkpoint or not',
                      default=False, type=bool)
  parser.add_argument('--checksession', dest='checksession',
                      help='checksession to load model',
                      default=1, type=int)
  parser.add_argument('--checkepoch', dest='checkepoch',
                      help='checkepoch to load model',
                      default=1, type=int)
  parser.add_argument('--checkpoint', dest='checkpoint',
                      help='checkpoint to load model',
                      default=0, type=int)
# log and diaplay
  parser.add_argument('--use_tfb', dest='use_tfboard',
                      help='whether use tensorboard',
                      action='store_true')

# save model and log
  parser.add_argument('--model_name',
                      help='directory to save models', required=True, type=str)
  parser.add_argument('--log_name',
                      help='directory to save logs', 
                      type=str)

  args = parser.parse_args()
  return args

class sampler(Sampler):
  def __init__(self, train_size, batch_size):
    self.num_data = train_size
    self.num_per_batch = int(train_size / batch_size)
    self.batch_size = batch_size
    self.range = torch.arange(0,batch_size).view(1, batch_size).long()
    self.leftover_flag = False
    if train_size % batch_size:
      self.leftover = torch.arange(self.num_per_batch*batch_size, train_size).long()
      self.leftover_flag = True

  def __iter__(self):
    rand_num = torch.randperm(self.num_per_batch).view(-1,1) * self.batch_size
    self.rand_num = rand_num.expand(self.num_per_batch, self.batch_size) + self.range

    self.rand_num_view = self.rand_num.view(-1)

    if self.leftover_flag:
      self.rand_num_view = torch.cat((self.rand_num_view, self.leftover),0)

    return iter(self.rand_num_view)

  def __len__(self):
    return self.num_data
  
  
if __name__ == '__main__':
  lr = [10e-5, 10e-4, 10e-6]
  lr_decay_step = [2,3]
  opt=['sdg','adam','RMSprop']
  layer=['layer 5', 'layer 6','first deeper layer','second deeper layer']
  combinations=list(itertools.product(lr,lr_decay_step, opt, layer))

  for c in range (len(combinations)):
    args = parse_args()
    

    print('Called with args:')
    print(args)

    if args.dataset == 'pascal_voc':
        args.imdb_name = 'voc_2007_train'
        args.imdb_name_val='voc_2007_val'
        args.imdbval_name = 'voc_2007_test'
        args.set_cfgs = ['ANCHOR_SCALES', '[8, 16, 32, 64]', 'ANCHOR_RATIOS', '[0.5,1,2]', 'MAX_NUM_GT_BOXES', '20']
    else:
        assert 'Unknown dataset error!'

    args.cfg_file = 'cfgs/{}_ls.yml'.format(args.net) if args.large_scale else 'cfgs/{}.yml'.format(args.net)
    if args.cfg_file is not None:
      cfg_from_file(args.cfg_file)
    if args.set_cfgs is not None:
      cfg_from_list(args.set_cfgs)

    print('Using config:')
    pprint.pprint(cfg)
    
    np.random.seed(cfg.RNG_SEED)

    if torch.cuda.is_available() and not args.cuda:
      print("WARNING: You have a CUDA device, so you should probably run with --cuda")

    #train set
    cfg.TRAIN.USE_FLIPPED = False
    cfg.USE_GPU_NMS = args.cuda
    imdb, roidb, ratio_list, ratio_index = combined_roidb(args.imdb_name)
  

    train_size = len(roidb)
    
    print('{:d} roidb entries train'.format(len(roidb)))
    
    # output path
    
    
    
    output_dir = args.save_dir + "/" + args.net + "_" + args.model_name + "/" + args.dataset + "/training" + str(c+1)
    print(f'\n---------> model output_dir = {output_dir}\n')
    if not os.path.exists(output_dir):
      os.makedirs(output_dir)

    sampler_batch = sampler(train_size, args.batch_size)

    dataset = roibatchLoader(roidb, ratio_list, ratio_index, args.batch_size, \
                            imdb.num_classes, training=True)

    dataloader = torch.utils.data.DataLoader(dataset, batch_size=args.batch_size,
                              sampler=sampler_batch, num_workers=args.num_workers)

    #validations set 
    

    cfg.USE_GPU_NMS = args.cuda
    
    imdb_val, roidb_val, ratio_list_val, ratio_index_val= combined_roidb(args.imdb_name_val)

    val_size=len(roidb_val)
    print('{:d} roidb entries val'.format(len(roidb_val)))
    

    # output path
    #output_dir = args.save_dir + "/" + args.net + "_" + args.model_name + "/" + args.dataset + "/training".format(c)
    #print(f'\n---------> model output_dir = {output_dir}\n')
    
    

    #if not os.path.exists(output_dir):
      #os.makedirs(output_dir)

    sampler_batch_val = sampler(val_size, args.batch_size)

    dataset_val = roibatchLoader(roidb_val, ratio_list_val, ratio_index_val, args.batch_size, \
                            imdb.num_classes, training=True)

    dataloader_val = torch.utils.data.DataLoader(dataset_val, batch_size=args.batch_size,
                              sampler=sampler_batch_val, num_workers=args.num_workers)

    if args.net == 'vgg16':
      fasterRCNN = vgg16(imdb.classes, pretrained=False, class_agnostic=args.class_agnostic)
    elif args.net == 'res101':
      fasterRCNN = resnet(imdb.classes, 101, pretrained=False, class_agnostic=args.class_agnostic)
    elif args.net == 'res50':
      fasterRCNN = resnet(imdb.classes, 50, pretrained=False, class_agnostic=args.class_agnostic)
    elif args.net == 'res152':
      fasterRCNN = resnet(imdb.classes, 152, pretrained=False, class_agnostic=args.class_agnostic)
    else:
      print("network is not defined")
      pdb.set_trace()

    fasterRCNN.create_architecture()
    model_dir = args.load_dir + "/" + args.net + "_handobj_100K" + "/" + args.dataset
    if not os.path.exists(model_dir):
      raise Exception('There is no input directory for loading network from ' + model_dir)
    load_name = os.path.join(model_dir, 'faster_rcnn_1_8_132028.pth'.format(args.checksession, args.checkepoch, args.checkpoint))

    print("load checkpoint %s" % (load_name))

    if args.cuda > 0:
      checkpoint = torch.load(load_name)
    else:
      checkpoint = torch.load(load_name, map_location=(lambda storage, loc: storage))
    fasterRCNN.load_state_dict(checkpoint['model'])
    if 'pooling_mode' in checkpoint.keys():
      cfg.POOLING_MODE = checkpoint['pooling_mode']

    print('load model successfully!')
   
    if combinations[c][3]== 'layer 5':
      for i,child in enumerate(fasterRCNN.children()):
        if i <= 5: 
          for param in child.parameters():
              param.requires_grad = False
    
    if combinations[c][3]== 'layer 6':
       for i,child in enumerate(fasterRCNN.children()):
        if i <= 5: 
          for param in child.parameters():
              param.requires_grad = False
        if i == 6:
          for j, grandchild in enumerate(child[0]):
            if j <= 2:
              for param in grandchild.parameters():
                  param.requires_grad= False
    
    if combinations[c][3]== 'first deeper layer':
       for i,child in enumerate(fasterRCNN.children()):
        if i <= 5: 
          for param in child.parameters():
              param.requires_grad = False
        if i == 6:
         for j, grandchild in enumerate(child[0]):
          if j <= 0:
            for param in grandchild.parameters():
                param.requires_grad= False
    
    if combinations[c][3]== 'second deeper layer':
       for i,child in enumerate(fasterRCNN.children()):
        if i <= 5: 
          for param in child.parameters():
              param.requires_grad = False
        if i == 6:
          for j, grandchild in enumerate(child[0]):
           if j <= 1:
            for param in grandchild.parameters():
              param.requires_grad= False
           
          
   
    # initilize the tensor holder here.
    im_data = torch.FloatTensor(1)
    im_info = torch.FloatTensor(1)
    num_boxes = torch.LongTensor(1)
    gt_boxes = torch.FloatTensor(1)
    box_info = torch.FloatTensor(1)
   

    # ship to cuda
    if args.cuda:
      im_data = im_data.cuda()
      im_info = im_info.cuda()
      num_boxes = num_boxes.cuda()
      gt_boxes = gt_boxes.cuda()
      box_info = box_info.cuda()


    # make variable
    im_data = Variable(im_data)
    im_info = Variable(im_info)
    num_boxes = Variable(num_boxes)
    gt_boxes = Variable(gt_boxes)
    box_info = Variable(box_info)

    lr = cfg.TRAIN.LEARNING_RATE
    #lr = args.lr
    lr = combinations [c][0]
    lr_decay_step= combinations[c][1]
    optimizer=combinations[c][2]
    params = []
    parameters=np.asarray([combinations [c][0], combinations[c][1], combinations[c][2], combinations[c][3]])
    with open('parameters.csv', 'w') as f:
        write = csv.writer(f)
        write.writerow(parameters)
      
    shutil.copy('parameters.csv', output_dir)
      
    for key, value in dict(fasterRCNN.named_parameters()).items():
      if value.requires_grad:
        if 'bias' in key:
          params += [{'params':[value],'lr':lr*(cfg.TRAIN.DOUBLE_BIAS + 1), \
                  'weight_decay': cfg.TRAIN.BIAS_DECAY and cfg.TRAIN.WEIGHT_DECAY or 0}]
        else:
          params += [{'params':[value],'lr':lr, 'weight_decay': cfg.TRAIN.WEIGHT_DECAY}]
    if args.cuda:
      fasterRCNN.cuda()
    

    
    if args.optimizer == "adam":
      lr = lr * 0.1
      optimizer = torch.optim.Adam(params)

    elif args.optimizer == "sgd":
      optimizer = torch.optim.SGD(params, momentum=cfg.TRAIN.MOMENTUM)
    
    elif args.optimizer == "RMSprop":
      optimizer = torch.optim.RMS.prop(params)
    
    iters_per_epoch = int(train_size / args.batch_size)
    iters_per_epoch_val = int(val_size / args.batch_size)
    
    

    loss_train_tot=[]
    rpn_loss_cls_train=[]
    rpn_loss_box_train=[]
    RCNN_loss_cls_train=[]
    RCNN_loss_bbox_train=[]
    loss_val_tot=[]
    rpn_loss_cls_val_tot=[]
    rpn_loss_box_val_tot=[]
    RCNN_loss_cls_val_tot=[]
    RCNN_loss_bbox_val_tot=[]

    
    
    for epoch in range(args.start_epoch, args.max_epochs + 1):
      # setting to train mode
      fasterRCNN.train()
      loss_temp = 0
      start = time.time()

      if epoch % (args.lr_decay_step + 1) == 0:
          adjust_learning_rate(optimizer, args.lr_decay_gamma)
          lr *= args.lr_decay_gamma

      data_iter = iter(dataloader)
      loss_train_tmp=[]
      rpn_loss_cls_train_tmp=[]
      rpn_loss_box_train_tmp=[]
      RCNN_loss_cls_train_tmp=[]
      RCNN_loss_bbox_train_tmp=[]
      loss_val_tmp=[]
      rpn_loss_cls_val_tmp=[]
      rpn_loss_box_val_tmp=[]
      RCNN_loss_cls_val_tmp=[]
      RCNN_loss_bbox_val_tmp=[]
      
      # for step in range(iters_per_epoch):
      for step in range(iters_per_epoch):
        
        data = next(data_iter)
        with torch.no_grad():
          im_data.resize_(data[0].size()).copy_(data[0])
          im_info.resize_(data[1].size()).copy_(data[1])
          gt_boxes.resize_(data[2].size()).copy_(data[2])
          num_boxes.resize_(data[3].size()).copy_(data[3])
          box_info.resize_(data[4].size()).copy_(data[4])

        fasterRCNN.zero_grad()
        rois, cls_prob, bbox_pred, \
        rpn_loss_cls, rpn_loss_box, \
        RCNN_loss_cls, RCNN_loss_bbox, \
        rois_label, loss_list = fasterRCNN(im_data, im_info, gt_boxes, num_boxes, box_info)
        

        loss_train = rpn_loss_cls.mean() + rpn_loss_box.mean() \
            + RCNN_loss_cls.mean() + RCNN_loss_bbox.mean()
        
        for score_loss in loss_list:
          if type(score_loss[1]) is not int:
            loss_train += score_loss[1].mean()
        loss_temp += loss_train.item()
        
        # backward
        optimizer.zero_grad()
        loss_train.backward()
        if args.net == "vgg16":
          clip_gradient(fasterRCNN, 10.)
        optimizer.step()
        if step % args.disp_interval == 0:
          end = time.time()
          if step > 0:
            loss_temp /= (args.disp_interval + 1)

          if args.mGPUs:
            loss_rpn_cls = rpn_loss_cls.mean().item()
            loss_rpn_box = rpn_loss_box.mean().item()
            loss_rcnn_cls = RCNN_loss_cls.mean().item()
            loss_rcnn_box = RCNN_loss_bbox.mean().item()
            loss_hand_state = 0 if type(loss_list[0][1]) is int else loss_list[0][1].mean().item()
            loss_hand_dydx = 0 if type(loss_list[1][1]) is int else loss_list[1][1].mean().item()
            loss_hand_lr = 0 if type(loss_list[2][1]) is int else loss_list[2][1].mean().item()
            fg_cnt = torch.sum(rois_label.data.ne(0))
            bg_cnt = rois_label.data.numel() - fg_cnt
          else:
            loss_rpn_cls = rpn_loss_cls.item()
            loss_rpn_box = rpn_loss_box.item()
            loss_rcnn_cls = RCNN_loss_cls.item()
            loss_rcnn_box = RCNN_loss_bbox.item()
            loss_hand_state = 0 if type(loss_list[0][1]) is int else loss_list[0][1].item()
            loss_hand_dydx = 0 if type(loss_list[1][1]) is int else loss_list[1][1].item()
            loss_hand_lr = 0 if type(loss_list[2][1]) is int else loss_list[2][1].item()
            fg_cnt = torch.sum(rois_label.data.ne(0))
            bg_cnt = rois_label.data.numel() - fg_cnt
        
        loss_train_tmp.append(loss_train.item())
        rpn_loss_cls_train_tmp.append(rpn_loss_cls.item())
        rpn_loss_box_train_tmp.append(rpn_loss_box.item())
        RCNN_loss_cls_train_tmp.append(RCNN_loss_cls.item())
        RCNN_loss_bbox_train_tmp.append(RCNN_loss_bbox.item())
        
      
        
  # setting to val mode
      fasterRCNN.train()
      loss_temp_val = 0



    
      data_iter_val = iter(dataloader_val)
      
      for step in range(iters_per_epoch_val):
       
        data_val = next(data_iter_val)
        with torch.no_grad():
          im_data.resize_(data_val[0].size()).copy_(data_val[0])
          im_info.resize_(data_val[1].size()).copy_(data_val[1])
          gt_boxes.resize_(data_val[2].size()).copy_(data_val[2])
          num_boxes.resize_(data_val[3].size()).copy_(data_val[3])
          box_info.resize_(data_val[4].size()).copy_(data_val[4])

        fasterRCNN.zero_grad()
        rois, cls_prob, bbox_pred, \
        rpn_loss_cls_val, rpn_loss_box_val, \
        RCNN_loss_cls_val, RCNN_loss_bbox_val, \
        rois_label_val, loss_list_val = fasterRCNN(im_data, im_info, gt_boxes, num_boxes, box_info)
        
        loss_val = rpn_loss_cls_val.mean() + rpn_loss_box_val.mean() \
            + RCNN_loss_cls_val.mean() + RCNN_loss_bbox_val.mean()
        
        for score_loss in loss_list_val:
          if type(score_loss[1]) is not int:
            
            loss_val += score_loss[1].mean()
        
        loss_temp_val += loss_val.item()
        # backward
        optimizer.zero_grad()
      
        loss_val_tmp.append(loss_val.item())
        rpn_loss_cls_val_tmp.append(rpn_loss_cls_val.item())
        rpn_loss_box_val_tmp.append(rpn_loss_box_val.item())
        RCNN_loss_cls_val_tmp.append(RCNN_loss_cls_val.item())
        RCNN_loss_bbox_val_tmp.append(RCNN_loss_bbox_val.item())

        


      
      loss_train_tot.append(np.mean(np.array(loss_train_tmp)))
      loss_val_tot.append(np.mean(np.array(loss_val_tmp)))
      rpn_loss_cls_train.append(np.mean(np.array(rpn_loss_cls_train_tmp)))
      rpn_loss_box_train.append(np.mean(np.array(rpn_loss_box_train_tmp)))
      RCNN_loss_cls_train.append(np.mean(np.array(RCNN_loss_cls_train_tmp)))
      RCNN_loss_bbox_train.append(np.mean(np.array(RCNN_loss_bbox_train_tmp)))
      rpn_loss_cls_val_tot.append(np.mean(np.array(rpn_loss_cls_val_tmp)))
      rpn_loss_box_val_tot.append(np.mean(np.array(rpn_loss_box_val_tmp)))
      RCNN_loss_cls_val_tot.append(np.mean(np.array(RCNN_loss_cls_val_tmp)))
      RCNN_loss_bbox_val_tot.append(np.mean(np.array(RCNN_loss_bbox_val_tmp)))
      
      save_name = os.path.join(output_dir, 'faster_rcnn_new_{}_{}.pth'.format(args.session, epoch, step))
      # output path loss

      
      #save_loss_train=os.path.join(output_dir_loss,'loss_train')
      #save_loss_val=os.path.join(output_dir_loss,'loss_val')
      with open('train_loss_tot.csv', 'w') as f:
        write = csv.writer(f)
        write.writerow(loss_train_tot)
      
      shutil.copy('train_loss_tot.csv', output_dir)
      
      with open('val_loss_tot.csv', 'w') as f:
        write = csv.writer(f)
        write.writerow(loss_val_tot)
      
      shutil.copy('val_loss_tot.csv', output_dir)

      
      
      

      
      
      
      save_checkpoint({
        'session': args.session,
        'epoch': epoch + 1,
        'model': fasterRCNN.module.state_dict() if args.mGPUs else fasterRCNN.state_dict(),
        'optimizer': optimizer.state_dict(),
        'pooling_mode': cfg.POOLING_MODE,
        'class_agnostic': args.class_agnostic,
      }, save_name)
      print('************epoch: %d************'%(epoch))
      print(loss_train_tot)
      print(loss_val_tot)
      print(rpn_loss_cls_train)
      print(rpn_loss_box_train)
      print(RCNN_loss_cls_train)
      print(RCNN_loss_bbox_train)
      print(rpn_loss_cls_val_tot)
      print(rpn_loss_box_val_tot)
      print(RCNN_loss_cls_val_tot)
      print(RCNN_loss_bbox_val_tot)
      


  print ('*******FINAL LOSS********')
  print('loss del train:', loss_train_tot)
  print('loss del val:', loss_val_tot)
  print('loss cls train:',rpn_loss_cls_train )
  print('loss box train:', rpn_loss_box_train)
  print('loss RCNN cls train:', RCNN_loss_cls_train)
  print('loss RCNN bbox train:', RCNN_loss_bbox_train)
  print('loss cls val:', rpn_loss_cls_val_tot)
  print('loss box val:',rpn_loss_box_val_tot)
  print('loss RCNN cls val:',RCNN_loss_cls_val_tot)
  print('loss RCNN boc val:', RCNN_loss_bbox_val_tot)
  save_loss_train= os.path.join(output_dir_loss, 'loss_train'.format(loss_train_tot))
  save_loss_val= os.path.join(output_dir_loss, 'loss_val'.format(loss_val_tot))
    
