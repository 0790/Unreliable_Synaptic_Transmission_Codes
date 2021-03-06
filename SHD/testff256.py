# -*- coding: utf-8 -*-
"""TestFeedForwardSNN-SHD.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1DY-_WpxZtD5vbyq0Zt2OLLOjOBll8Ep6
"""

#implementing feed forward spiking neural network as described in Surrogate Gradient Learning in Spiking Neural Networks.
#https://arxiv.org/abs/1901.09948

#using LIF neurons, setting hyperparameters.
#number of neurons in each layer N = 128
#learning rate alpha = 0.001
#betas for Adam optimizer, first and second momemt = 0.9, 0.999
#batch size for minibatch Nbatch = 256
#threshold potential Uthres = 1
#reset potential Urest = 0
#synaptic time constant tausyn = 10 ms = 0.01 s
#membrane time constant taumem = 20 ms = 0.02 s
#let the exponential form of time constants be lambd and muh
#refractory time constant tref = 0 ms
#time step size = 1 ms = 0.001 s
#total simulation duration = 1s

import os
import h5py

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec
import seaborn as sns

import torch
import torch.nn as nn
import torchvision
from torch.utils import data

import pickle
#train_file = h5py.File('/content/drive/MyDrive/dataset/shd_train.h5','r')
#test_file = h5py.File('/content/drive/MyDrive/dataset/shd_test.h5','r')

#cache_dir = os.path.expanduser("~/data")
#cache_subdir = "hdspikes"
basepath = os.getcwd()

direc = "/dataset/shddataset/"
print(basepath+direc)
#file_path = get_shd_dataset(cache_dir = basepath, cache_subdir =direc)

#directrain = "~/dataset/shddataset/shd_train.h5"
#directest = "~/dataset/shddataset/shd_test.h5"
#print(file_path)

train_file = h5py.File((basepath + direc +  "shd_train.h5"), 'r')
test_file = h5py.File((basepath +direc + "shd_test.h5"), 'r')


x_train = train_file['spikes']
y_train = train_file['labels']
x_test = test_file['spikes']
y_test = test_file['labels']

"""import tables
fileh = tables.open_file('/content/drive/MyDrive/dataset/shd_train.h5', mode='r')
units = fileh.root.spikes.units
times = fileh.root.spikes.times
labels = fileh.root.labels
index = 0
print("Times (ms):", times[index])
print("Unit IDs:", units[index])
print("Label:", labels[index])
import matplotlib.pyplot as plt

fig = plt.figure(figsize=(16,4))
idx = np.random.randint(len(times),size=5)
for i,k in enumerate(idx):
	ax = plt.subplot(1,5,i+1)
	ax.scatter(times[k],700-units[k], color="k", alpha=0.33, s=2)
	ax.set_title("Label %i"%labels[k])
	ax.axis("off")

plt.show()
"""

N = 256 #per layer
Nin = 700 #input from 700 bushy cells
Nout = 20 #0-9 in english and german
alpha = 0.0001/2
beta1 = 0.9
beta2 = 0.999
Nbatch = 256


Uthres = 1
Urest = 0
tausyn = 0.005
taumem =  0.01
tref = 0
steep = 100

t= 0.001
T = 1.5
Ntimesteps = 100 #int(T/t) #check to 100 if nothing works

lambd = float(np.exp(-t/tausyn))
muh = float(np.exp(-t/taumem))

datatype = torch.float
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

x_train.keys()

x_train["times"][:]

#size of weight matrix : w1 = (700,128) w2 = (128,20)
#https://pytorch.org/docs/stable/generated/torch.empty.html
w1 = torch.empty((Nin,N), dtype = datatype , device = device , requires_grad= True) #require_grad = true to record the operations on the weight matrix
w2 = torch.empty((N,Nout), dtype = datatype , device = device , requires_grad= True) #transpose of this is multiplied with incoming spikes
loss_hist = []

#normalize using He initialization
#https://pytorch.org/docs/stable/nn.init.html
print("made weight variables and loss histogram")



if os.path.isfile((basepath+"/trained_values/trained256.pt")):
	print('The file is present.')
	w1,w2 = torch.load(basepath+'/trained_values/trained256.pt')
else:
	print("File not present")
	torch.nn.init.uniform_(w1, a=-np.sqrt(2.0/Nin), b=np.sqrt(2.0/Nin)) #given as uniform distribution in papers, but normal distribution in spytorch code
	torch.nn.init.uniform_(w2, a=-np.sqrt(2.0/N), b=np.sqrt(2.0/N))
	print("Uniform distribution initialization for weights")

Nepochs = 150 #450
#450 epochs to check lr = 0.00005 next round 150


#read weights and loss from file, if empty, set pass to 0, else pass to 1 and put stored weights and histogram to local variables and  train


import torch.autograd as auto
#https://pytorch.org/docs/stable/autograd.html
#https://snntorch.readthedocs.io/en/latest/snntorch.surrogate.html

class SurGrad(auto.Function):
	@staticmethod
	def forward(ctx,i):
		ctx.save_for_backward(i)
		result = torch.zeros_like(i)
		result[i>Uthres] = 1.0
		return result

	@staticmethod
	def backward(ctx, grad_output):
		result, = ctx.saved_tensors #U is stored in results
		grad_input = grad_output.clone()
		grad = grad_input/(steep*torch.abs(result) +1.0 )**2
		return grad

spikefunction = SurGrad.apply



def sparse_data_generator_from_hdf5_spikes(X, y, batch_size, nb_steps, nb_units, max_time, shuffle=True):
	labels_ = np.array(y,dtype=int)
	number_of_batches = len(labels_)//batch_size
	sample_index = np.arange(len(labels_))

    	# compute discrete firing times
	firing_times = X['times']
	units_fired = X['units']

	time_bins = np.linspace(0, max_time, num=nb_steps)
	if shuffle:
		np.random.shuffle(sample_index)

	total_batch_count = 0
	counter = 0
	while counter<number_of_batches:
		batch_index = sample_index[batch_size*counter:batch_size*(counter+1)]

		coo = [ [] for i in range(3) ]
		for bc,idx in enumerate(batch_index):
			times = np.digitize(firing_times[idx], time_bins)
			units = units_fired[idx]
			batch = [bc for _ in range(len(times))]
			coo[0].extend(batch)
			coo[1].extend(times)
			coo[2].extend(units)

		i = torch.LongTensor(coo).to(device)
		v = torch.FloatTensor(np.ones(len(coo[0]))).to(device)
		X_batch = torch.sparse.FloatTensor(i, v, torch.Size([batch_size,nb_steps,nb_units])).to(device)
		y_batch = torch.tensor(labels_[batch_index],device=device)
		yield X_batch.to(device=device), y_batch.to(device=device)
		counter += 1




#forward pass for the SNN
def forwarddynamic(input):
	Isyn1 = torch.zeros((Nbatch,N), dtype= datatype , device = device)
	Umem1 = torch.zeros((Nbatch,N), dtype= datatype , device = device)
	#membrane potential and synaptic current will have same dimensions as the weights.T in a mini batch  gradient descent
	Isynnext1 = torch.zeros((Nbatch,N), dtype= datatype , device = device)
	Umemnext1 = torch.zeros((Nbatch,N), dtype= datatype , device = device)
	Urecord1 = []
	Spikerecord = []


	#hidden layer 1
	outputlayer1 = torch.zeros((Nbatch,N), dtype= datatype , device = device)
	z1_from_input = torch.einsum("ijk,kl->ijl" , (input, w1))
	"""i gives the spike train number, j and k describe the firing time and the neuron unit which fires. k and l describes the dimesnions of weight matrix. k is
	the index of multiplication ==> dot product"""
	for t in range(Ntimesteps):
		z1 = z1_from_input[:,t]
		outputlayer1 = spikefunction(Umem1)
		resetspike = outputlayer1.detach() #what does this do?

		Isynnext1 = lambd*Isyn1 + z1 # I[t+1] = lambda * I[t] + w1*inputspike  (no V matrix for feedforward)
		Umemnext1 = (muh*Umem1+Isyn1)*(1-resetspike)

		Spikerecord.append(outputlayer1)
		Urecord1.append(Umem1)

		Umem1 = Umemnext1
		Isyn1 = Isynnext1


	Urecord1 = torch.stack(Urecord1,dim=1)
	Spikerecord = torch.stack(Spikerecord, dim=1)
	#hidden unit to output layer, output layer is leaky integrator which don't spike
	#purpose of this layer is to record the membrane potential of the last layer
	z2 = torch.einsum("ijk,kl->ijl" , (Spikerecord , w2) )
	Isyn2 = torch.zeros((Nbatch,Nout), dtype= datatype , device = device)
	Umem2 = torch.zeros((Nbatch,Nout), dtype= datatype , device = device)
	output_potential = [Umem2]
	for t in range(Ntimesteps):
		Isynnext2 = lambd*Isyn2 + z2[:,t]
		Umemnext2 = muh* Umem2 + Isyn2

		Isyn2 = Isynnext2
		Umem2 = Umemnext2
		output_potential.append(Umem2)
	output_potential = torch.stack(output_potential,dim=1)
	records = [Urecord1 , Spikerecord]
	return output_potential , records

def training(x , y , alpha= alpha , Nepochs = 10):
	parameters = [w1,w2]

	#using softmax function for negative likelihood loss calculation
	loss_record = []
	for i in range(Nepochs):
		local_loss = []
		for x_local, y_local in sparse_data_generator_from_hdf5_spikes(X = x,y = y,batch_size= Nbatch, nb_steps= Ntimesteps,nb_units= Nin,max_time= T):
			output,records = forwarddynamic(x_local.to_dense())
			_,spikes = records #this is used in regularization
			#https://pytorch.org/docs/stable/generated/torch.max.html
			maximum,_ = torch.max(output,dim =1)  #calculates maximum membrane potential in the entire duration
			logsoftmax = nn.LogSoftmax(dim=1)
			y_network =logsoftmax(maximum)
			#strength of the spike limit is given by 2 x 10^ -6
			reg = 2e-6*torch.sum(spikes) + 2e-6*torch.mean(torch.sum(torch.sum(spikes,dim=0),dim=0)**2)
			if device == 'cpu':
				y_local = y_local.type(torch.LongTensor)
			elif device == 'cuda':
				y_local = y_local.type(torch.cuda.LongTensor)
			#https://pytorch.org/docs/stable/generated/torch.nn.NLLLoss.html
			loss = nn.NLLLoss()(y_network , y_local)
			loss = loss + reg
			optim = torch.optim.Adamax(parameters, lr = alpha, betas = (beta1,beta2))
			optim.zero_grad() #sets the gradient of optimised weights to 0. https://pytorch.org/docs/stable/generated/torch.optim.Adam.html
			loss.backward()
			optim.step()
			local_loss.append(loss.item())
		mean_loss = np.mean(local_loss)
		loss_record.append(mean_loss)
		print("Epoch %i: loss=%.5f"%(i+1,mean_loss))
	return loss_record,parameters


def accuracy(x,y):
        acc = []
        for x_local, y_local in sparse_data_generator_from_hdf5_spikes(X =x,y= y,batch_size = Nbatch, nb_steps = Ntimesteps, nb_units = Nin, max_time = T,shuffle=False):
                output,_ = forwarddynamic(x_local.to_dense())
                maximum,_ = torch.max(output,dim =1)  #calculates maximum membrane potential in the entire duration
                _,y_unit = torch.max(maximum,1) #the unit which had maximu
                tmp = np.mean((y_local==y_unit).detach().cpu().numpy()) # compare to labels
                acc.append(tmp)
        return np.mean(acc)

def plotloss(loss_hist):
	plt.figure()
	plt.plot(loss_hist, label="Loss")
	plt.xlabel("Epoch")
	plt.ylabel("Loss")
	plt.legend()


loss_list,_ = training(x_train, y_train)
loss_hist.append(loss_list)


print("Trained for 10 epochs")
print("Training accuracy: %.3f"%(accuracy(x_train,y_train)))

loss_list,[rw1,rw2] = training(x_train, y_train , Nepochs = Nepochs)
loss_hist.append(loss_list)
print("Training accuracy: %.3f"%(accuracy(x_train,y_train)))
print("Test accuracy: %.3f"%(accuracy(x_test,y_test)))
torch.save([rw1,rw2] , basepath+'/trained_values/trained256.pt')
open_file = open((basepath+"/trained_values/trainedhist256.pkl"),"ab")
pickle.dump(loss_list,open_file)
print("\nFile list dumped\n")
open_file.close()
print("Saved in file")
open_file= open(basepath+"/trained_values/trainedhist256.pkl","rb")
a = pickle.load(open_file)
print(a)
print("256 units code and uniform weight stores correctly? ")

