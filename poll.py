import subprocess
from subprocess import run, PIPE, STDOUT
import sched
import datetime, time

avg_poll_interval = 0.25 # [unit: secs]
n_avgs            = 5/avg_poll_interval
log_poll_interval = 60   # [unit: secs]
query_fields = ['power.draw', 'temperature.gpu', '']
gpuids = [] # replace with query on startup

def initialize():
    global gpuids, limits
    gpuids = [0]

    # query static limits for temp, power ...
    # TODO
    limits = {
        0: { # gpuid
            'memory': 12204,    # [unit: MiB]
            'power': 250,       # [unit: W]
            'temperature': {    # [unit: C]
                'slowdown': 92,
                'shutdown': 97,
            },
        },
    }


avgidx = 0
def query(sc):
    global avgidx
    avgidx += 1
    for gpuid in gpuids:
        args = ['nvidia-smi', '--format=csv,noheader,nounits', '--id='+str(gpuid), '--query-gpu='+','.join(query_fields)]
        result = run(args, stderr=STDOUT, stdout=PIPE, encoding='utf-8')
        print(str(gpuid) + ': ' + result.stdout.replace('\n',''))
    if (avgidx < n_avgs):
        sc.enter(avg_poll_interval, 1, query, (sc,))

def log(sc):
    # initialize and start average polling
    print("acquiring average\n"+','.join(query_fields))
    sc_avg = sched.scheduler(time.time, time.sleep)
    sc_avg.enter(0, 1, query, (sc_avg,))
    sc.enter(log_poll_interval, 1, log, (sc,))
    sc_avg.run()


if __name__ == '__main__':
    # initialize and start log polling
    initialize()
    sc_log = sched.scheduler(time.time, time.sleep)
    sc_log.enter(0, 1, log, (sc_log,))
    sc_log.run()
