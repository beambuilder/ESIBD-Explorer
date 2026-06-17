# pylint: disable=[missing-module-docstring]  # see class docstrings
#################### Imports  ##########################################
import time
import os
import re
from datetime import datetime
import numpy as np

class GA():
    """Genetic Algorithm for Optimization of Almost Everything
    by ioneater (ioneater.dev@gmail.com)
    Version: 3.2 (2013-2022 / re implemented in Python in 2020)
    Adopted for use with PyQt applications"""
    def __init__(self):
        #################### Private Variables  ##################################
        self.population              = []    # array of all beings
        self.seeds                   = []    # array of all parameters to optimize with restrictions
        self.seed_dict               = {}    # dictionary to relate label to seed
        self.current_being           = 0     # index of current being (counting up during generation)
        self.current_generation      = 1     # current generation counter

        self.pop_size                = 0     # population size
        self.num_elite               = 0     # number of parents that survive a generation unchanged
        self.p_rank                  = []    # probabilities to select parent of certain rank
        self.rescan                  = 1     # if =1 also reevaluates the parents
        self._rescan_every           = 0     # Defines the number of generations after which the parents will be rescanned. Default is 0 (disabled).
        self._converged              = 0     # count for how many steps best_fitness has not increased
        self.first_run               = True  # only True during first generation (also first generation after restart)
        self._maximize               = True  # max or minimize

        self.fit_best                = 0     # best fitness since last fitness function change
        self.beststring = ''
        self._best_fitness_array     = []    # array of best fitness values
        self._avg_fitness_array      = []    # array of average fitness values
        self._gen_seconds_array      = []    # array of times in ms for each generation
        self._file_path              = ''    # file path used to store the results
        self._file_name              = 'GA'  # file name used to store the results
        self._restore                = False  # if True the last saved configuration will be restored

        self.bestfile                = ''
        self.restorefile             = ''
        # self.terminate  # Simion specific parameter that is not implemented in python version
        self.init()




    #################### Private Functions  ################################

    def select_parent(self):
        parent=0
        if np.random.rand() < 0.5:  # tournament selection (best of 3 random picked)
            r = np.random.randint(self.num_elite)  # get random integer in range [1, num_elite]
            parent=r
            for i in np.arange(2):
                r = np.random.randint(self.num_elite)
                parent = r if r < parent else parent  # keep lowes index==highest fitness as parent
        else:  # rank weighting
            r=np.random.rand()
            for i, p in enumerate(self.p_rank):
                if p > r:
                    parent = i
                    break
        return parent



    def mix(self, a, b):  # continuous mixing of parameters from different parents
        beta=np.random.rand()
        return beta*a+(1-beta)*b,(1-beta)*a+beta*b


    def crossover(self):
        for i in np.arange(self.pop_size)[self.num_elite::2]:  # iterate over every second child (always generate two at a time)
            ma=self.select_parent()
            pa=self.select_parent()
            while ma == pa:  # make sure to avoid incest
                pa=self.select_parent()
            for j in np.arange(len(self.seeds)):
                self.population[i].values[j], self.population[i+1].values[j]=self.mix(self.population[ma].values[j], self.population[pa].values[j])







    def mutate(self):
        for being in self.population[self.num_elite:]:  # iterate over children
            for i, seed in enumerate(self.seeds):  # iterate over parameters
                being.values[i]=noise(being.values[i], seed)





    def round_to_n(self, x, n):#round_to_n = lambda x, n: np.round(x,-int(np.floor(np.log10(x))) + (n - 1)) has problems with 0 and inf
        if x == 0 or x == np.inf or x == -np.inf:
            return x
        else:
            return np.round(x,-int(np.floor(np.log10(np.abs(x)))) + (n - 1))



    def save_session(self):
        """always save current state in files"""
        try:
            best_file=open(self.bestfile, 'a', encoding = 'utf-8')  # append
            restore_file=open(self.restorefile, 'w', encoding = 'utf-8')  # overwrite
        except Exception as e:
            print(f'GA: could not save session: {e}')
            return
        self.beststring = ''# save fitness and chromosome of best candidate of each generation
        restorestring = '%10s'%'best fit'  # save num_elite best ones fore restoring simulation
        for label in [s.label for s in self.seeds]:
            restorestring=restorestring + '%15s'%label
        restore_file.write('generation %i\n'%self.current_generation)
        restore_file.write(restorestring + '\n')  # write header for columns
        if self.first_run:
            best_file.write('%4s%20s%10s%s\n'%('gen','date     time','avg fit', restorestring))  # write header for columns
        for i in np.arange(self.num_elite):
            restorestring='%10.2f'%self.population[i].fitness
            for j in np.arange(len(self.seeds)):
                restorestring=restorestring + '%15.3e'%self.population[i].values[j]
            if i==0:
                best_file.write('%4d%20s%10.2f%s\n'%(self.current_generation, datetime.now().strftime("%m/%d/%Y %H:%M:%S"), self.average_fitness(), restorestring))
            restore_file.write(restorestring + '\n')
        best_file.close()
        restore_file.close()
        print('GA: Session Saved -- Average Fitness: %6.2f Best Fitness: %6.2f'%(self.average_fitness(), self.best_fitness()))
        self.first_run=False







    def restore_session(self):  # restore only if activated by user
        if os.path.exists(self.restorefile):
            restore_file=open(self.restorefile, 'r', encoding = 'utf-8')
        else:
            restore_file=False
        if restore_file and self._restore:  # adopt parents from file and create children  #  always evaluate parents after restoring session since fitness function might have changed
            self.current_generation= 1 + int(re.search('(\d+)', restore_file.readline()).group(1))  # additional information from first row
            restore_file.readline()  #skip header
            for i in np.arange(self.num_elite):
                # skip fitness as evaluated in last run -- reject since fitness function and many other things might have cchanged and a fresh evaluation is needed
                for j, n in enumerate(restore_file.readline().split()[1:]):
                    self.population[i].values[j]=float(n)
            restore_file.close()
            print('GA: session ' + self._file_name + ' restored')
            print('GA: ATTENTION: parameters are loaded from ' + self._file_name + '_restore.txt')
            self.crossover()
            self.mutate()
        else:
            print('GA: session ' + self._file_name + ' initialized')

    ################### Public Variables  ##################################

    def maximize(self, maximize=None):
        if maximize is not None:
            self._maximize = maximize
            self.fit_best= -np.inf if self._maximize else np.inf
        else:
            return self._maximize


    def generation(self):
        return self.current_generation


    def GAget(self, label, default: float  = 0, index = -1, initial = False):  # provide default in case this parameter is not optimized and index to return custom being, typically 0 for best parent
        if label in self.seed_dict:
            if initial:
                return self.seeds[self.seed_dict[label]].initial  # return initial parameter
            else:
                return self.round_to_n(self.population[self.current_being if index == -1 else index].values[self.seed_dict[label]], 6)  # return values rounded to 6 significant digits
        else:
            return default  # allows to quickly change between optimized parameters without changing the implementation around GAget




    def best_fitness(self):  # make sure to sort population before calling this to incude recent results
        return self.population[0].fitness  # note python counts from 0


    def average_fitness(self):  # average of parents fitness
        return np.mean([b.fitness for b in self.population[:self.num_elite]])




    def fitness(self, fitness=None):  # sets or gets fitness of current being
        if fitness is not None:
            self.population[self.current_being].fitness=fitness
        else:
            return self.round_to_n(self.population[self.current_being].fitness, 6)  # round to 6 significant digits



    def best_fitness_array(self):
        return self._best_fitness_array


    def avg_fitness_array(self):
        return self._avg_fitness_array


    def gen_seconds_array(self):
        return self._gen_seconds_array


    def file_path(self, file_path=None):
        if self.file_path is not None:
            self._file_path=file_path
            self.updatePaths()
            if not os.path.isdir(file_path):
                os.mkdir(self._file_path)  # does throw error if already exists
        else:
            return self._file_path


    def file_name(self, file_name=None):
        if file_name is not None:
            self._file_name=file_name
            self.updatePaths()
        else:
            return self._file_name

    def updatePaths(self):
        self.bestfile = self._file_path + '/' + self._file_name + '_best.txt'
        self.restorefile = self._file_path + '/' + self._file_name + '_restore.txt'


    def rescan_every(self, rescan_every=None):
        if rescan_every is not None:
            self._rescan_every=rescan_every
        else:
            return self._rescan_every


    def restore(self, restore=None):
        if restore is not None:
            self._restore=restore


    def converged(self):
        return self._converged

    def new_generation(self):  # Returns True if a new generation just started
        return (self.rescan == 1 and self.current_being == 0) or ((self.rescan !=1 or not self._rescan_every) and self.current_being == self.num_elite)



    ################### Public Functions  ##################################

    def init(self):  # restore initial state
        np.random.seed()
        self.population            = []
        self.seeds                 = []
        self.seed_dict             = {}
        self.current_being         = -1
        self.current_generation    = 1

        self.pop_size              = 0
        self.num_elite             = 0
        self.p_rank                = []
        self.rescan                = 1
        self._rescan_every         = 0
        self._converged            = 0
        self.first_run             = True
        self._maximize             = True

        self.fit_best              = -np.inf if self._maximize else np.inf
        self._best_fitness_array   = []
        self._avg_fitness_array    = []
        self._gen_seconds_array    = []
        self._file_path            = ''  # 'C:/Users/srgroup/Desktop'
        self._file_name            = 'GA'
        self._restore              = False




    def optimize(self, initial: float = 0, _min: float = 0, _max: float = 100, _rate: float = .2, _range: float = 10, label=''):  # Adds a parameter for optimization. name for access via GA.name
        if label in self.seed_dict:  # update existing parameter
            self.seeds[self.seed_dict[label]]=Seed(initial, _min, _max, _rate, _range, label)
        else:  # add new parameter
            self.seeds.append(Seed(initial, _min, _max, _rate, _range, label))
            self.seed_dict[label]=len(self.seeds)-1
            if _rate > 0:# do not consider inactive parameters
                self.pop_size=self.pop_size+4


    def genesis(self):
        self.pop_size=max(self.pop_size, 10)
        self.num_elite=int(max(self.pop_size/2, 6))
        self.p_rank=np.zeros(self.num_elite)  ## p_rank[0] is used only for calculation in next line
        for i in np.arange(self.num_elite):
            self.p_rank[i]=(self.num_elite-i)/(np.arange(self.num_elite)+1).sum()+(self.p_rank[i-1] if i>0 else 0)
        for i in np.arange(self.pop_size):
            self.population.append(Being(self._maximize, self.seeds, i==0))  # keep original settings in first being and add noise to others
        self.restore_session()
        print('GA: Starting Generation %i:'%self.current_generation)





    def check_restart(self, _terminate=False):
        """Returns True if the next setting should be applied and tested. The second return parameter indicates if a new session has started"""
        if self.current_being < self.pop_size-1 and not _terminate:
            self.current_being=self.current_being+1
            #print('increased current_being to', current_being)
            return True, False  # i.e. fly again with chromosome of current_being
        else:  # initialize new generation
            self.population.sort(key=lambda being: being.fitness, reverse = self._maximize)
            self._converged = self._converged + 1 if self.best_fitness() <= self.fit_best else 0  # count up if fitness not improved otherwise reset
            self.fit_best = self.fit_best if self._converged != 0 else self.best_fitness()
            self._best_fitness_array.append(self.best_fitness())
            self._avg_fitness_array .append(self.average_fitness())
            self._gen_seconds_array .append(int(round(time.time())))  # time in s
            self.at_generation_completed()
            self.save_session()
            if _terminate:
                self.current_being=0  # make sure GAget returns best parameters even without explicit index
                return False, True
            else:  # initialize new generation
                self.current_generation=self.current_generation+1
                print('GA: Starting Generation %i:'%self.current_generation)
                if self._rescan_every != 0:
                    self.rescan=1 if self.rescan==self._rescan_every else self.rescan+1
                # reevaluate parents every _rescan_every generations since results may be irreproducible due to noise, otherwise evaluate childrens only
                self.current_being = self.num_elite if self._rescan_every == 0 or self.rescan != 1 else 0
                self.crossover()
                self.mutate()
                return True, True






    def rescan_parents(self):  # Rescan parents when the fitness function has been changed by the user program.
        self.current_being=1
        self._converged=0
        self.rescan=1

    def GAprint(self, a):  # Prints to sdt out and also writes to the GA log file.
        best=open(self.file_path + '/' + self.file_name + '_best.txt','a', encoding='utf-8')  # append
        print('GA: ' + a)
        best.write(a + '\n')
        best.close()


    def print_step(self):  # Prints the index and fitness of the current being to std out
        print(self.step_string())

    def step_string(self):  # Prints the index and fitness of the current being to std out
        return 'GA: %d.%d Fitness = %6.2f'%(self.generation(), self.current_being, self.population[self.current_being].fitness)


    def at_generation_completed(self):
        # This function will be executed at the end of each generation. Redefine it in your program e.g. to print additional results or define custom termination condition.
        pass



#################### Classes  ##########################################



class Seed():  # contains optimization parameters for each parameter. number of seeds corresponds to number of parameters to optimize
    def __init__(self, initial, _min, _max, _rate, _range, label):
        self.initial = initial
        self.min    = _min
        self.max    = _max
        self.rate   = _rate
        self.range  = _range
        self.label   = label








class Being():  # contains the parameter set and fitness. number of beings corresponds to population size
    def __init__(self, maximize, seeds, keep=False):
        self.values=[]
        self.fitness= -np.inf if maximize else np.inf  # make sure that non evaluated beings end up at the bottom of the list after sorting
        for seed in seeds:
            if keep:
                self.values.append(seed.initial)
            else:
                self.values.append(noise(seed.initial, seed))

##################################### Function  #################################################

def noise(value, seed):
    if np.random.rand() < seed.rate:
        return max(min(value+np.random.rand()*2*seed.range-seed.range, seed.max), seed.min)
    else:
        return value
