import boto3
import random
import string
from valve.rcon import RCON

# https://boto3.amazonaws.com/v1/documentation/api/latest/guide/migrationec2.html#launching-new-instances

# TODO if we fail to get a client / instance, retry in this order. If we don't get any of them, abort.
REGION_PRIORITIES = ['us-east-2', 'us-east-1', 'us-west-2', 'us-west-1']
# TODO not in any real order
INSTANCE_PRIORITIES = ["c5.large", "t3.small", "t3a.small", "c5a.large"]

class EC2Instance:
    def __init__(self, ip, id):
        self.ec2_credentials = {"ec2-ip" : ip, "instance-id" : id}
        self.ec2_misc = dict()

    def run_command(self, client, command):
        response = client.send_command(
            InstanceIds=[self.ec2_credentials["instance-id"]],
            DocumentName="AWS-RunShellScript",
            Parameters={'commands': [command]})

        command_id = response['Command']['CommandId']
        output = ssm_client.get_command_invocation(CommandId=command_id, InstanceId=self.ec2_credentials["instance-id"])
        print(output)

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

        return EC2Instance(instance.get("PublicIpAddress", ""), instance.get("InstanceId", ""))

class TF2Interface:
    def __init__(self, ip, port, rcon):
        self.ip_port = (ip, port)
        self.rcon = rcon
        self.client = RCON(self.ip_port, rcon)
        self.client.connect()
        self.client.authenticate()

    def rcon_command(self, command):
        response = self.client.execute(command)
        return response

    def close(self):
        self.client.close()