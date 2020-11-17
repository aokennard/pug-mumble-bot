import pymumble_py3 as pymumble
from pymumble_py3.callbacks import PYMUMBLE_CLBK_TEXTMESSAGERECEIVED as RCV

import time

commands = pymumble.commands.Commands()

def message_received(proto_message):
    # https://github.com/azlux/pymumble/blob/pymumble_py3/pymumble_py3/mumble_pb2.py
    commands.new_cmd(pymumble.messages.TextMessage(proto_message.actor, proto_message.channel_id, proto_message.message))

class MumbleBot:
    def __init__(self, server_ip, server_port, nickname, password):
        self.mumble_client = pymumble.Mumble(server_ip, nickname, password=password, port=server_port)
        self.mumble_client.callbacks.set_callback(RCV, message_received)

    def start(self):
        self.mumble_client.start()
        self.mumble_client.is_ready()

    def stop(self):
        self.mumble_client.stop()

    def start_pug_command(self):
        pass

    def end_pug_command(self):
        pass

    def kick_user(self, user):
        pass

    def ban_user(self, user, time=None):
        pass

    def execute_rcon_command(self, command):
        pass
    
if __name__ == '__main__':
    bot = MumbleBot('negasora.com', 64735, 'testbot', '')
    bot.start()
    while True:
        if commands.is_cmd():
            new_cmd = commands.pop_cmd()
            if new_cmd != None:
                print(new_cmd.parameters)
                message = new_cmd.parameters["message"]
                if message == "quit":
                    break
    bot.stop()