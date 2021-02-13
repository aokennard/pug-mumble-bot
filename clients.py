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
        self.server_up = False

    def create_tags(self, tags):
        self._instance.create_tags(Resources=[self.ec2_credentials["instance-id"]], Tags=tags)

    def get_ip(self):
        return self.ec2_credentials["ec2-ip"]

    def get_tf2_pass(self):
        return self.ec2_misc["tf2_pass"]

    def set_tf2_pass(self, pw):
        self.ec2_misc["tf2_pass"] = pw
        
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
    def turn_off_server(self, client):
        self.run_command(client, "killall srcds_linux")
        self.run_command(client, "killall srcds_run")
        self.server_up = False


class EC2Interface:
    def __init__(self, key_id="", access_key=""):
        self.ec2_client = self.setup_client('ec2', key_id, access_key)
        self.ssm_client = self.setup_client('ssm', key_id, access_key)
        #self.instance_to_ip_id = dict()
        self.ec2_instance_pool = []

    # no support for region priorities atm
    def setup_client(self, client_type, key_id, access_key):
        return boto3.client(client_type, 
            region_name=REGION_PRIORITIES[0], 
            aws_access_key_id=key_id, 
            aws_secret_access_key=access_key)

    def get_instance_from_ip(self, ip):
        instance_dict = self.ec2_client.describe_instances()
        for instance in instance_dict["Reservations"][0]["Instances"]:
            if instance["PublicIpAddress"] == ip:
                return EC2Instance(instance, ip)
        return None

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
        
        instance = conn.get("Instances")
        print(instance)
        if not instance:
            print("Failed to create EC2 instance")
            return None

        waiter = self.ec2_client.get_waiter('instance_running')
        waiter.wait(InstanceIds=[instance[0]['InstanceId']])
        
        response = self.ec2_client.describe_instances(InstanceIds=[instance[0]['InstanceId']])
        public_ip = response["Reservations"][0]["Instances"][0]["PublicIpAddress"]

        # set mumble / bot IP in envvar for tf2 server / other use?
        new_instance = EC2Instance(instance[0], public_ip)
        return new_instance

    # Monitor people currently in lobby or mumble as a whole, may not necessarily spin down
    def monitor_mumble_spin_down(self, ec2_instance, mumble_client):
        self.ec2_instance_pool.append(ec2_instance)
        # Work - naive for now, make better later TODO
        time.sleep(60)
        
        active_players = len(mumble_client.get_lobby_users(use_chill_room=False))
        for pug in config["max_pugs"]:
            active_players += mumble_client.get_pug_users(pug)

        turn_off_instance = active_players < config["min_total_players"]

        if turn_off_instance:
            print("Mumble monitor turning off instance")
            self.turn_off_instance(ec2_instance)
            if ec2_instance in self.ec2_instance_pool:
                self.ec2_instance_pool.remove(ec2_instance)
        else:
            print("Mumble monitor is keeping instance alive")
            
    def spin_down_instance(self, ec2_instance, use_mumble_monitoring=False, mumble_client=None):
        if use_mumble_monitoring:
            if mumble_client:
                self.monitor_mumble_spin_down(ec2_instance, mumble_client)
                return
    
        print("Now performing spin down default spin down")
        self.turn_off_instance(ec2_instance)

    def turn_off_instance(self, instance):
        instance_id = instance.ec2_credentials["instance-id"]
        instance.turn_off_server(self.ssm_client)

        try:
            #self.ec2_client.release_address(AllocationId=self.instance_to_ip_id[instance_id])
            self.ec2_client.terminate_instances(InstanceIds=[instance_id])
        except ClientError as e:
            print(e)
        
        #del self.instance_to_ip_id[instance_id]    

class TF2Interface:
    def __init__(self, ip, port, rcon):
        self.ip_port = (ip, port)
        self.rcon = rcon
        self.client = RCON(self.ip_port, rcon)

    def await_connect_to_server(self):
        retry_count = 0
        while retry_count < 10:
            try:
                self.client = RCON(self.ip_port, self.rcon)
                self.auth()
                return True
            except Exception as e:
                print(e)
                print("Failed connecting to tf2, retrying ", retry_count)
                retry_count += 1
                self.close()
                time.sleep(20)
        return False

    def update_rcon(self, new_rcon):
        self.rcon_command("rcon_password {}".format(new_rcon))
        self.rcon = new_rcon
        self.client = RCON(self.ip_port, self.rcon)
        self.auth()

    def rcon_command(self, command):
        response = self.client.execute(command)
        return response

    def auth(self):
        self.client.connect()
        self.client.authenticate()

    def close(self):
        self.client.close()
