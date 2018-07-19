import subprocess
from subprocess import run, PIPE, STDOUT
import sched
import datetime, time

avg_poll_interval = 0.1 # [unit: secs]
n_avgs            = 20
log_poll_interval = 10   # [unit: secs]
query_fields = ['power.draw', 'temperature.gpu', '']
gpuids = [] # replace with query on startup

def query_gpu(fields, gpuid=None, keep_newlines=False):
    """query all or a specific gpu for information and return as string"""
    args = ['nvidia-smi', '--query-gpu={!s}'.format(','.join(fields)), '--format=csv,noheader,nounits']
    if gpuid is not None:
        args.insert(1, '--id={:d}'.format(int(gpuid)))
    result = bytes.decode(subprocess.check_output(args), 'utf-8')
    if not keep_newlines:
        result = result.replace('\n','')
    return result

def initialize():
    """query each device for limits and capacities"""
    global gpuids, limits
    ngpu = int(query_gpu(['count']))
    gpuids = list(range(ngpu))
    print('Identified '+str(ngpu)+' GPU(s)')

    # query static limits for temp, power ...
    gpu_details = {}
    for gpuid in gpuids:
        q = query_gpu(['gpu_name','gpu_serial','gpu_uuid','memory.total','power.limit'], gpuid).split(',')
        temp_lims = bytes.decode(subprocess.check_output('nvidia-smi -q --display=TEMPERATURE | awk \'BEGIN{FS=" *: *";OFS=","}; /Shutdown Temp/ {sub(" C","",$2); stop=$2}; /Slowdown Temp/ {sub(" C", "", $2); slow=$2}; END{OFS=","; print slow,stop}\'', shell=True), 'utf-8').replace('\n','').split(',')
        d = {}
        d['model']  = q[0]
        d['serial'] = q[1]
        d['uuid']   = q[2]
        d['limits'] = {
            'memory': q[3],    # [unit: MiB]
            'power':  q[4],     # [unit: W]
            'temperature': {    # [unit: C]
                'slowdown': temp_lims[0],
                'shutdown': temp_lims[1],
            },
        }
        gpu_details[gpuid] = d
        print(d)

def query(sc, qidx):
    qidx += 1
    for gpuid in gpuids:
        args = ['nvidia-smi', '--format=csv,noheader,nounits', '--id='+str(gpuid), '--query-gpu='+','.join(query_fields)]
        result = run(args, stderr=STDOUT, stdout=PIPE, encoding='utf-8')
        print(str(gpuid) + ': ' + result.stdout.replace('\n',''))
    if (qidx < n_avgs):
        sc.enter(avg_poll_interval, 1, query, (sc,qidx))

def log(sc):
    # initialize and start average polling
    print("acquiring average\n"+','.join(query_fields))
    sc_avg = sched.scheduler(time.time, time.sleep)
    avgidx = 0
    sc_avg.enter(0, 1, query, (sc_avg,avgidx))
    sc_avg.run()
    sc.enter(log_poll_interval, 1, log, (sc,))


if __name__ == '__main__':
    # initialize and start log polling
    initialize()
    sc_log = sched.scheduler(time.time, time.sleep)
    sc_log.enter(0, 1, log, (sc_log,))
    sc_log.run()
