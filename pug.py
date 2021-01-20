from clients import EC2Instance, TF2Interface
from config import config
import string
import random

DEFAULT_PASSWORD_LENGTH = config["password_length"]
DEFAULT_RCON_LENGTH = config["rcon_length"]
DEFAULT_PORT = config["tf2_port"]

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
    def __init__(self, pug_number):
        self.pug_number = pug_number
        self.pug_state = PugState.NOT_STARTED
        self.players = {config["RED"] : [], config["BLU"] : []}
        
        self.connect_pass = generate_random_string(DEFAULT_PASSWORD_LENGTH)
        self.rcon = generate_random_string(DEFAULT_RCON_LENGTH)

    def set_ip(self, ip):
        self.connect_ip = ip

    def get_tf2_client(self):
        return TF2Interface(self.connect_ip, DEFAULT_PORT, self.rcon)
