import json
import subprocess


def _call(what='active'):
    ps = subprocess\
        .Popen(['celery','-A',\
                'pvgrip',\
                'inspect',what,'-j'],
               stdout = subprocess.PIPE)
    res = subprocess.run(['head','-n','-2'],
                         stdin = ps.stdout,
                         stdout = subprocess.PIPE)
    return json.loads(res.stdout.decode())


def status():
    return {'active': _call('active'),
            'scheduled': _call('scheduled')}


