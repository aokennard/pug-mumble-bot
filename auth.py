from config import config

DEFAULT_KEY_ID_FILENAME = config["aws_key_id_filename"]
DEFAULT_ACCESS_KEY_FILENAME = config["aws_access_key_filename"]

def read_file_secrets(filename):
    with open(filename, "r") as f:
        return f.read()

def get_access_key(filename=DEFAULT_ACCESS_KEY_FILENAME):
    return read_file_secrets(filename)

def get_aws_key_id(filename=DEFAULT_KEY_ID_FILENAME):
    return read_file_secrets(filename)
