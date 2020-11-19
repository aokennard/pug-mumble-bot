import pymumble_py3 as pymumble
from pymumble_py3.messages import TextMessage
from pymumble_py3.callbacks import PYMUMBLE_CLBK_TEXTMESSAGERECEIVED as RCV

from clients import EC2Interface

import auth
import pug

import argparse

commands = pymumble.commands.Commands()

BOT_COMMANDS = {"kick" : kick_user,
                "ban"  : ban_user}

def message_received(proto_message):
    # https://github.com/azlux/pymumble/blob/pymumble_py3/pymumble_py3/mumble_pb2.py#L1060
    commands.new_cmd(TextMessage(proto_message.actor, proto_message.channel_id, proto_message.message))

class MumbleBot:
    # TODO - internal state of the server kept here.
    # Root
    #   Lobby
    #       Queue
    #       Not playing
    #       Volunteer?
    #   Pug <N>
    #       Blue
    #       Red
    #   Chill rool / Admin
    # Each of these should have a num people + actor ID's if possible
    # TODO connections to EC2 as well as RCON for servers, + passwords
    def __init__(self, server_ip, server_port, nickname, password):
        self.mumble_client = pymumble.Mumble(server_ip, nickname, password=password, port=server_port)
        self.pugs = []
        self.ec2_interface = EC2Interface(auth.get_aws_key_id(), auth.get_access_key())
        self.setup_callbacks()
    
    def setup_callbacks(self):
        self.mumble_client.callbacks.set_callback(RCV, message_received)

    def start(self):
        self.mumble_client.start()
        self.mumble_client.is_ready()

    def stop(self):
        self.mumble_client.stop()

    def error_message(self, *args):
        print(args)

    def process_message(self, message):
        message_split = message.split()

        process_function = BOT_COMMANDS.get(message_split[0], error_message)
        self.process_function(message_split[1:])

    def start_pug_command(self, pug_number):
        # Creates new mumble channels for a pug, under the main 'Pug channel' - red / blu
        # TODO should users have permissions to move 
        new_ec2_instance = self.ec2_interface.create_ec2_instance()
        if not new_ec2_instance:
            print("Unable to create ec2 instance, send help")
            return False

        new_pug = pug.Pug(new_ec2_instance)

        # Spins up new EC2 instance, pre-imaged with TF2 server (CDK OR cli?)
        # Have multiple? callbacks at this point - return upon EC2 instance starting up w/ tf2 server
        # Generated RCON + PW for the instance, sets them
        

        # Picking logic
        # have callbacks keep track of the number of people moved into channels
        # TODO Have a volunteer phase (users join channels, use separate command, or are in a queue of volunteers in separate channel any1 can join?)
        # Provite immunity to volunteers
        # After some amount of time / volunteer command called (whether by command or queue?), roll remainder medics. Calculated as 2 - sum(people in RED/BLU channels) people to roll.
        # note: if above is negative, goto the return of the picking logic?
        # TODO Need to check an immunity list for medics, have separate command for emptying the list, or doing it after N pugs? Deliberate
        # TODO account for subs / edge cases?

        
        # Sending info: either
        # Once an appropriate number are in a pugs channel, and the pug hasn't entered a 'started' state: wait a few seconds (edge cases?) and send to each user OR
        # As soon as tf2 server is started returns w/ callback, set pugN channels info to connect.
        # TODO decide if we want to send data to the bot to say if the pug started, or just set after sending connect.
        pass


    def end_pug_command(self, *args):
        # args = [pug_number]

        # This is likely received as a command from the TF2 SM plugin.
        # Explicitly deletes the pugN channels, clears relevant pugN data for mumble.
        # Dumps people to lobby channel (after some time?)
        # tells EC2 instance to spin down, probably. 
        # Maybe do some logic that makes the server wait a few minutes before shutting down, to save time in case another pug starts
        # Possible: wait a 1-5 minutes to keep track of num people in lobby after a pug ends, to see if people will leave. If it looks like its ending, spin down.

    def kick_user(self, *args):
        # args = [user, reason=opt]
        # Kick from the mumble server
        pass

    def ban_user(self, *args):
        # args = [user, reason=opt, time=opt]
        # Ban from the mumble server
        pass

    def toggle_mute(self, *args):
        # Mute / unmute the people in lobby / not playing
        pass

    def execute_rcon_command(self, *args):
        # args = [pug_number, command]
        # Execute an RCON command from mumble
        # TODO make life easier? - be able to just 'start_pug_map process', have it changelevel and execute config
        pass
    
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Create a mumble bot for a designated server")
    parser.add_argument('--host', type=str, help="A string of the server IP/hostname", default='negasora.com')
    parser.add_argument('--port', type=int, help="An int of the servers port", default=64735)
    parser.add_argument('--name', type=str, help="Optional bot name", default='testbot')
    parser.add_argument('--pw', type=str, help="Optional password for server", default='')
    args = parser.parse_args()

    bot = MumbleBot(args.host, args.port, args.name, args.pw)
    bot.start()
    while True:
        if commands.is_cmd():
            new_cmd = commands.pop_cmd()
            if new_cmd != None:
                message = new_cmd.parameters["message"]
                if message == "quit":
                    break
                bot.process_message(message)
    bot.stop()