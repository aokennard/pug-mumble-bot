
DEFAULT_KEY_ID_FILENAME = "secrets/aws_key_id"
DEFAULT_ACCESS_KEY_FILENAME = "secrets/aws_access_key"

def read_file_secrets(filename):
    with open(filename, "r") as f:
        return f.read()

def get_access_key(filename=DEFAULT_ACCESS_KEY_FILENAME):
    return read_file_secrets(filename)

def get_aws_key_id(filename=DEFAULT_KEY_ID_FILENAME):
    return read_file_secrets(filename)
