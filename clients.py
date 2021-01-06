import boto3
from botocore.exceptions import ClientError
import random
import string
import socket
import time
from valve.rcon import RCON

from config import config

# https://boto3.amazonaws.com/v1/documentation/api/latest/guide/migrationec2.html#launching-new-instances

# TODO if we fail to get a client / instance, retry in this order. If we don't get any of them, abort.
REGION_PRIORITIES = config["region_priorities"]
# TODO not in any real order
INSTANCE_PRIORITIES = config["instance_priorities"]

RETRY_DELAY = 10
RETRIES = 10

class EC2Instance:
    def __init__(self, instance):
        self._instance = instance
        self.ec2_credentials = {"ec2-ip" : instance.get("PublicIpAddress"), "instance-id" : instance.get("InstanceId")}
        self.ec2_misc = dict()

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
        try:
            output = ssm_client.get_command_invocation(CommandId=command_id, InstanceId=self.ec2_credentials["instance-id"])
        except ClientError as e:
            print(e)
        print(output)

    # because wait_until_running seems not good right now?
    def await_instance_startup(self, retry_delay=RETRY_DELAY, retries=RETRIES):
        retry_count = 0
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if not sock:
            print("Error creating socket")
            return
        while retry_count < retry_delay:
            result = sock.connect_ex((self._instance.public_ip_address, 22))
            if result == 0:
                break
            time.sleep(retry_delay)
            retry_count += 1
        sock.close()

    # TODO turn off tf2 + misc
    def turn_off_server(self):
        pass


class EC2Interface:
    def __init__(self, key_id="", access_key=""):
        self.ec2_client = self.setup_client('ec2', key_id, access_key)
        self.ssm_client = self.setup_client('ssm', key_id, access_key)
        self.instance_to_ip_id = dict()
        self.ec2_instance_pool = set()

    def setup_client(self, client_type, key_id, access_key):
        return boto3.client(client_type, 
            region_name=REGION_PRIORITIES[0], 
            aws_access_key_id=key_id, 
            aws_secret_access_key=access_key)

    def create_ec2_instance(self):
        if len(self.ec2_instance_pool) != 0:
            return self.ec2_instance_pool.pop()

        # alternatively, get existing instance and just turn it on.
        
        conn = self.ec2_client.run_instances(InstanceType="t2.micro", 
                            MaxCount=1, 
                            MinCount=1, 
                            ImageId="ami-0b59bfac6be064b78") 

        instance = conn.get("Instances", None)
        if not instance:
            print("Failed to create EC2 instance")
            return None

        try:
            allocation = ec2.allocate_address(Domain='vpc')
            response = ec2.associate_address(AllocationId=allocation['AllocationId'], InstanceId=instance['InstanceId'])
        except ClientError as e:
            print(e)
        self.instance_to_ip_id[instance['InstanceId']] = allocation['AllocationId']

        new_instance = EC2Instance(instance)
        return new_instance

    # Monitor people currently in lobby or mumble as a whole, may not necessarily spin down
    def monitor_mumble_spin_down(self, ec2_instance, mumble_client):
        self.ec2_instance_pool.append(ec2_instance)

        turn_off_instance = True

        # Work - naive for now, make better later TODO
        time.sleep(60)

        turn_off_instance = mumble_client.users.count() < 12

        if turn_off_instance and ec2_instance in self.ec2_instance_pool:
            self.ec2_instance_pool.remove(ec2_instance)
            ec2_instance.turn_off_server()

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
        self.client.connect()
        self.client.authenticate()

    def rcon_command(self, command):
        response = self.client.execute(command)
        return response

    def close(self):
        self.client.close()

if __name__ == '__main__':
    pass
