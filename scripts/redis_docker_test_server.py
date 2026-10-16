"""stdin-compatible PrivateRedis command using an owned ephemeral container.

Set CAMPUSBRIDGE_TEST_REDIS_COMMAND to [python, this_script]. No existing Redis
service or volume is used. Password stays in stdin, never command arguments.
"""
import os
import re
import subprocess
import sys
import uuid


def command(config, docker):
    port=re.search(r'^port ([0-9]+)$',config,re.MULTILINE)
    if not port or not 1024<int(port[1])<65536 or 'bind 127.0.0.1\n' not in config:
        raise ValueError('PRIVATE_LOOPBACK_REDIS_CONFIG_REQUIRED')
    argv=[docker,'run','--rm','--pull=never','--init','-i','--name','cb-d4-test-redis-'+uuid.uuid4().hex,
          '--publish','127.0.0.1:'+port[1]+':'+port[1], 'redis:7.2-alpine','redis-server','-']
    return argv,config.replace('bind 127.0.0.1\n','bind 0.0.0.0\n',1)


def main():
    config=sys.stdin.read()
    argv,config=command(config,os.environ.get('CAMPUSBRIDGE_TEST_DOCKER','docker'))
    # PrivateRedis owns this child and sends Redis SHUTDOWN on fixture teardown.
    result=subprocess.run(argv,input=config,text=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    raise SystemExit(result.returncode)


if __name__=='__main__':main()
