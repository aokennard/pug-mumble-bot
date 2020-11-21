from clients import EC2Instance, TF2Interface
from enum import Enum

DEFAULT_PASSWORD_LENGTH = 12
DEFAULT_RCON_LENGTH = 20
DEFAULT_PORT = 27015

class PugState:
    INVALID = -1
    NOT_STARTED = 0
    TF2_SERVER_ACTIVE = 1
    TF2_SERVER_IN_USE = 2
    PUG_FINISHED = 3


def generate_random_string(length):
    alphabet = string.ascii_letters + string.digits
    return ''.join(random.choice(alphabet) for i in range(length))

class Pug:
    def __init__(self, pug_number, ec2_instance):
        self.pug_number = pug_number
        self.pug_state = PugState.NOT_STARTED
        self.players = {"RED" : [], "BLU" : []}

        self.connect_ip = ec2_instance.ec2_credentials["ec2-ip"]
        self.connect_pass = generate_random_string(DEFAULT_PASSWORD_LENGTH)
        self.rcon = generate_random_string(DEFAULT_RCON_LENGTH)
        
        self.ec2_instance = ec2_instance
        self.tf2_client = None

    def start_tf2_client(self):
        self.tf2_client = TF2Interface(self.connect_ip, DEFAULT_PORT, self.rcon)
