import boto3
import random
import string
import socket
import time
from valve.rcon import RCON

# https://boto3.amazonaws.com/v1/documentation/api/latest/guide/migrationec2.html#launching-new-instances

# TODO if we fail to get a client / instance, retry in this order. If we don't get any of them, abort.
REGION_PRIORITIES = ['us-east-2', 'us-east-1', 'us-west-2', 'us-west-1']
# TODO not in any real order
INSTANCE_PRIORITIES = ["c5.large", "t3.small", "t3a.small", "c5a.large"]

RETRY_DELAY = 10
RETRIES = 10

class EC2Instance:
    def __init__(self, instance):
        self._instance = instance
        self.ec2_credentials = {"ec2-ip" : instance.get("PublicIpAddress"), "instance-id" : instance.get("InstanceId")}
        self.ec2_misc = dict()

    def run_command(self, client, command):
        response = client.send_command(
            InstanceIds=[self.ec2_credentials["instance-id"]],
            DocumentName="AWS-RunShellScript",
            Parameters={'commands': [command]})

        command_id = response['Command']['CommandId']
        output = ssm_client.get_command_invocation(CommandId=command_id, InstanceId=self.ec2_credentials["instance-id"])
        print(output)

    # because wait_until_running seems not good right now?
    def await_instance_startup(self, retry_delay=RETRY_DELAY, retries=RETRIES):
        retry_count = 0
        while retry_count < retry_delay:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            result = sock.connect_ex((self._instance.public_ip_address, 22))
            if result == 0:
                break
            time.sleep(retry_delay)
            retry_count += 1

class EC2Interface:
    def __init__(self, key_id="", access_key=""):
        self.ec2_client = self.setup_client('ec2', key_id, access_key)
        self.ssm_client = self.setup_client('ssm', key_id, access_key)

    def setup_client(self, client_type, key_id, access_key):
        return boto3.client(client_type, 
            region_name=REGION_PRIORITIES[0], 
            aws_access_key_id=key_id, 
            aws_secret_access_key=access_key)

    def create_ec2_instance(self):
        # need to assign publicipaddress - create in subnet with this property TODO
        conn = self.ec2_client.run_instances(InstanceType="t2.micro", 
                            MaxCount=1, 
                            MinCount=1, 
                            ImageId="ami-0b59bfac6be064b78") 

        instance = conn.get("Instances", None)
        if not instance:
            print("Failed to create EC2 instance")
            return None

        return EC2Instance(instance)

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
