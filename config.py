import yaml 

class Config(dict):
    def __init__(self, filename):
        self.filename = filename
        self.load_config()

    def load_config(self):
        self.config = yaml.safe_load(open(self.filename))
    
    def __getitem__(self, key):
        self.load_config()
        return self.config[key]

config = Config("config.yml")