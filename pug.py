from clients import EC2Instance, TF2Interface

DEFAULT_PASSWORD_LENGTH = 12
DEFAULT_RCON_LENGTH = 20

def generate_random_string(length):
    alphabet = string.ascii_letters + string.digits
    return ''.join(random.choice(alphabet) for i in range(length))

class Pug:
    def __init__(self, ec2_instance):
        self.players = {"RED" : [], "BLU" : []}

        self.connect_ip = ec2_instance.ec2_credentials["ec2-ip"]
        self.connect_pass = generate_random_string(DEFAULT_PASSWORD_LENGTH)
        self.rcon = generate_random_string(DEFAULT_RCON_LENGTH)
        
        self.ec2_instance = ec2_instance
        self.tf2_client = TF2Interface(self.connect_ip, self.rcon)
