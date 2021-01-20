import boto3
from botocore.exceptions import ClientError
from valve.rcon import RCON

import random
import string
import socket
import time

from config import config

# https://boto3.amazonaws.com/v1/documentation/api/latest/guide/migrationec2.html#launching-new-instances

# TODO if we fail to get a client / instance, retry in this order. If we don't get any of them, abort.
REGION_PRIORITIES = config["region_priorities"]
# TODO not in any real order
INSTANCE_PRIORITIES = config["instance_priorities"]

RETRY_DELAY = config["ec2_retry_delay"]
RETRIES = config["ec2_num_retries"]

class EC2Instance:
    def __init__(self, instance, ip):
        self._instance = instance
        self.ec2_credentials = {"ec2-ip" : ip, "instance-id" : instance.get("InstanceId")}
        self.ec2_misc = dict()

    def create_tags(self, tags):
        self._instance.create_tags(Resources=[self.ec2_credentials["instance-id"]], Tags=tags)

    def get_ip(self):
        return self.ec2_credentials["ec2-ip"]
        
    def run_command(self, client, command):
        try:
            response = client.send_command(
                InstanceIds=[self.ec2_credentials["instance-id"]],
                DocumentName="AWS-RunShellScript",
                Parameters={'commands': [command]})
        except ClientError as e:
            print(e)
            return

        command_id = response['Command']['CommandId']
        time.sleep(5)
        try:
            output = client.get_command_invocation(CommandId=command_id, InstanceId=self.ec2_credentials["instance-id"])
            print(output)
        except ClientError as e:
            print(e)
        

    # because wait_until_running seems not good right now?
    def await_instance_startup(self, retry_delay=RETRY_DELAY, retries=RETRIES):
        retry_count = 0
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if not sock:
            print("Error creating socket")
            return
        while retry_count < retry_delay:
            result = sock.connect_ex((self.ec2_credentials["ec2-ip"], 22))
            if result == 0:
                break
            time.sleep(retry_delay)
            retry_count += 1
        sock.close()

    # TODO turn off tf2 + misc 
    def turn_off_server(self):
        self.run_command("killall srcds_linux")


class EC2Interface:
    def __init__(self, key_id="", access_key=""):
        self.ec2_client = self.setup_client('ec2', key_id, access_key)
        self.ssm_client = self.setup_client('ssm', key_id, access_key)
        self.instance_to_ip_id = dict()
        self.ec2_instance_pool = []

    # no support for region priorities atm
    def setup_client(self, client_type, key_id, access_key):
        return boto3.client(client_type, 
            region_name=REGION_PRIORITIES[0], 
            aws_access_key_id=key_id, 
            aws_secret_access_key=access_key)

    def create_ec2_instance(self, init_script=None):
        if len(self.ec2_instance_pool) != 0:
            return self.ec2_instance_pool.pop()

        # alternatively, get existing instance and just turn it on.
        # no instance backup support atm
        conn = self.ec2_client.run_instances(InstanceType=INSTANCE_PRIORITIES[0], 
                            MaxCount=1, 
                            MinCount=1,
                            SecurityGroupIds=config["ec2_secgroupids"],
                            ImageId=config["ec2_ami"],
                            KeyName="pootis-proxy-key", 
                            IamInstanceProfile={
                                'Arn': 'arn:aws:iam::588801620431:instance-profile/AmazonSSMRoleForInstancesQuickSetup', 
                            }) 
        
        instance = conn.get("Instances", None)
        if not instance:
            print("Failed to create EC2 instance")
            return None

        waiter = self.ec2_client.get_waiter('instance_running')
        waiter.wait(InstanceIds=[instance[0]['InstanceId']])

        # NOTE - max of 5 Elastic IPs, keep tabs on that
        try:
            allocation = self.ec2_client.allocate_address(Domain='vpc')
            response = self.ec2_client.associate_address(AllocationId=allocation['AllocationId'], InstanceId=instance[0]['InstanceId'])
        except ClientError as e:
            print(e)
        self.instance_to_ip_id[instance[0]['InstanceId']] = allocation['AllocationId']
        print("Created new instance class")

        # set mumble / bot IP in envvar for tf2 server / other use?
        new_instance = EC2Instance(instance[0], allocation["PublicIp"])
        return new_instance

    # Monitor people currently in lobby or mumble as a whole, may not necessarily spin down
    def monitor_mumble_spin_down(self, ec2_instance, mumble_client):
        self.ec2_instance_pool.append(ec2_instance)

        turn_off_instance = True

        # Work - naive for now, make better later TODO
        time.sleep(60)

        turn_off_instance = mumble_client.users.count() < config["min_total_players"]

        if turn_off_instance:
            del self.instance_to_ip_id[ec2_instance.ec2_credentials["instance-id"]]
            ec2_instance.turn_off_server()
            if ec2_instance in self.ec2_instance_pool:
                self.ec2_instance_pool.remove(ec2_instance)
            

    def spin_down_instance(self, ec2_instance, use_mumble_monitoring=False, mumble_client=None):
        if use_mumble_monitoring:
            if mumble_client:
                self.monitor_mumble_spin_down(ec2_instance, mumble_client)
                return
            print("Now performing spin down default spin down")

        instance_id = ec2_instance.ec2_credentials["instance-id"]

        try:
            self.ec2_client.release_address(AllocationId=self.instance_to_ip_id[instance_id])
            self.ec2_client.stop_instances(InstanceIds=[instance_id])
        except ClientError as e:
            print(e)
        
        del self.instance_to_ip_id[instance_id]
        ec2_instance.turn_off_server()
        

class TF2Interface:
    def __init__(self, ip, port, rcon):
        self.ip_port = (ip, port)
        self.rcon = rcon
        self.client = RCON(self.ip_port, rcon)

    def await_connect_to_server(self):
        # TODO run multiple times?
        retry_count = 0
        while retry_count < 10:
            try:
                self.client = RCON(self.ip_port, self.rcon)
                self.client.connect()
                self.client.authenticate()
                return True
            except Exception as e:
                print(e)
                print("Failed connecting to tf2, retrying ", retry_count)
                retry_count += 1
                time.sleep(20)
        return False

    def rcon_command(self, command):
        response = self.client.execute(command)
        return response

    def close(self):
        self.client.close()
