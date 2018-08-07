import sys, os
import logging
import subprocess
from subprocess import run, PIPE, STDOUT
import sched
import datetime, time
from pymongo import MongoClient

# Fixed paths
LOGS = './logs/'
os.makedirs(LOGS, exist_ok=True)

# setup logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler(sys.stdout))
logger.addHandler(logging.FileHandler(os.path.join(LOGS, 'poll.log')))

# connect to mongodb
dbclient = MongoClient('localhost', 3001)
try:
    dbclient.admin.command('ismaster')
except Exception as e:
    logger.warning("Connecting to fallback database")
    dbclient = MongoClient()
    dbclient.admin.command('ismaster')
db = dbclient['meteor']
db_gpu_usage = db['gpu_usage']
db_gpu_props = db['gpu_props']

# configuration
sample_poll_interval = 1 # time between samples in avg [unit: secs]
n_samples            = 3  # number of samples included in each average
log_poll_interval    = 60  # time between averages [unit: secs]
query_fields = ['utilization.gpu', 'utilization.memory', 'clocks.current.video', 'clocks.current.graphics', 'clocks.current.memory', 'clocks.current.sm', 'memory.total', 'memory.used', 'memory.free', 'fan.speed', 'power.draw', 'temperature.gpu'] # nvidia-smi fields to query

# dynamically set
gpuids = [] # replace with query on startup
db_fields = list(map(lambda x: str.replace(x,'.','_'), query_fields))

def clear_db():
    logger.debug("Deleting all data from database")
    db_gpu_props.delete_many({})
    db_gpu_usage.delete_many({})

def query_gpu(fields, gpuid=None, keep_newlines=False):
    """query all or a specific gpu for information and return as string"""
    args = ['nvidia-smi', '--query-gpu={!s}'.format(','.join(fields)), '--format=csv,noheader,nounits']
    if gpuid is not None:
        args.insert(1, '--id={:d}'.format(int(gpuid)))
    raw_result = bytes.decode(subprocess.check_output(args), 'utf-8')
    if not keep_newlines:
        raw_result = raw_result.replace('\n','')
    result = list(map(str.strip, raw_result.split(',')))
    if len(result)<2: return result[0]
    else: return result

def initialize():
    """query each device for limits and capacities"""
    global gpuids
    ngpu = int(query_gpu(['count'], 0))
    gpuids = list(range(ngpu))
    logger.info('Identified '+str(ngpu)+' GPU(s)')

    # query static limits for temp, power ...
    for gpuid in gpuids:
        q = query_gpu(['gpu_name','gpu_serial','gpu_uuid','memory.total','power.limit'], gpuid)
        temp_lims = bytes.decode(subprocess.check_output('nvidia-smi -q --display=TEMPERATURE | awk \'BEGIN{FS=" *: *";OFS=","}; /Shutdown Temp/ {sub(" C","",$2); stop=$2}; /Slowdown Temp/ {sub(" C", "", $2); slow=$2}; END{OFS=","; print slow,stop}\'', shell=True), 'utf-8').replace('\n','').split(',')
        map(str.strip, temp_lims)
        d_fixed = {
            'gpuid': gpuid,
            'model': q[0],
            'serial': q[1],
            'uuid': q[2],
        }
        d_variable = {
            'limits': {
                'memory': float(q[3]), # [unit: MiB]
                'power':  float(q[4]), # [unit: W]
                'temperature': {       # [unit: C]
                    'slowdown': float(temp_lims[0]),
                    'shutdown': float(temp_lims[1]),
                },
            }
        }
        logger.info({**d_fixed, **d_variable})
        db_gpu_props.update_one({'uuid': d_fixed['uuid']}, {'$set': d_variable, '$setOnInsert': d_fixed}, upsert=True)
        # TODO: check for existing props before adding

def get_sample(sc, idx, samples):
    idx += 1
    for gpuid in gpuids:
        dev_fields = ['serial', 'uuid']
        dev_result = query_gpu(dev_fields, gpuid=gpuid)
        data = query_gpu(query_fields, gpuid=gpuid)
        log_data = {
            'time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S:%f'),
            **{dev_fields[i]: dev_result[i] for i in range(len(dev_fields))},
            **{db_fields[i]: data[i] for i in range(len(db_fields))},
        }
        samples[gpuid].append(log_data)
        #  db_gpu_usage.insert_one(log_data)
    if (idx < n_samples):
        sc.enter(sample_poll_interval, 1, get_sample, (sc,idx,samples))

def get_average(sc):
    # initialize and start average polling
    sc_sample = sched.scheduler(time.time, time.sleep)
    sampleidx = 0
    samples = [[]*len(gpuids)]
    sc_sample.enter(0, 1, get_sample, (sc_sample,sampleidx,samples))
    sc_sample.run()

    # combine samples into average
    dbdata = []
    for gpusamples in samples:
        avg = gpusamples[-1].copy()
        for sample in gpusamples[:-1]:
            for field in db_fields:
                avg[field] = float(avg[field]) + float(sample[field])
        for field in db_fields:
            avg[field] /= len(gpusamples)
        logger.debug('Average: ' + str(avg))
        dbdata.append(avg)

    # commit to database
    db_gpu_usage.insert_many(dbdata)

    # loop indefinitely
    sc.enter(log_poll_interval, 1, get_average, (sc,))


if __name__ == '__main__':
    # initialize and start log polling
    initialize()
    logger.info("polling...")
    sc_log = sched.scheduler(time.time, time.sleep)
    sc_log.enter(0, 1, get_average, (sc_log,))
    sc_log.run()
