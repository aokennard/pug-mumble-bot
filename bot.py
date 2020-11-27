import pymumble_py3 as pymumble
from pymumble_py3.messages import TextMessage, MoveCmd
from pymumble_py3.constants import PYMUMBLE_MSG_TYPES_USERSTATS
from pymumble_py3.callbacks import PYMUMBLE_CLBK_TEXTMESSAGERECEIVED as RCV

from clients import EC2Interface
import auth
import pug

import threading
import argparse

ROOT_NAME = "Root"
LOBBY_NAME = "Lobby"
PUG_FORMAT_NAME = "Pug {}"
BLU_CHANNEL_NAME = "BLU"
RED_CHANNEL_NAME = "RED"

def get_bot_commands(scope):
    return {"kick" : scope.kick_user,
            "ban"  : scope.ban_user,
            "mesg" : scope.message_channel,
            "ping" : scope.ping_users}

class MumbleBot:
    def __init__(self, server_ip, server_port, nickname, password):
        self.mumble_client = pymumble.Mumble(server_ip, nickname, password=password, port=server_port)
        self.pugs = []
        self.user_set_tmp = []
        self.ec2_interface = EC2Interface(auth.get_aws_key_id(), auth.get_access_key())
        self.setup_callbacks()
    
    def message_received(self, proto_message):
        # https://github.com/azlux/pymumble/blob/pymumble_py3/pymumble_py3/mumble_pb2.py#L1060
        self.user_set_tmp.append(proto_message.actor)
        self.process_message(proto_message.message)
        self.mumble_client.users[proto_message.session[0]].send_text_message("yo")
        #self.mumble_client.commands.new_cmd(TextMessage(proto_message.actor, proto_message.channel_id, proto_message.message))

    def setup_callbacks(self):
        self.mumble_client.callbacks.set_callback(RCV, self.message_received)

    def start(self):
        self.mumble_client.start()
        self.mumble_client.is_ready()

    def stop(self):
        self.mumble_client.stop()
 
    def create_pug_channels(pug_number):
        channels = self.mumble_client.channels

        def get_or_create_channel(channel, parent, temporary=True):
            return channels.find_by_name(channel) or channels.new_channel(parent, channel, temporary)

        # bot exists in Root
        root_channel = get_or_create_channel(ROOT_NAME, self.mumble_client.my_channel(), temporary=False)
        lobby_channel = get_or_create_channel(LOBBY_NAME, root_channel, temporary=False)
        
        new_pug_channel = get_or_create_channel(PUG_FORMAT_NAME.format(pug_number), lobby_channel)

        new_blu_channel = get_or_create_channel(BLU_CHANNEL_NAME, new_pug_channel)
        new_red_channel = get_or_create_channel(RED_CHANNEL_NAME, new_pug_channel)

    def error_message(self, *args):
        print("Invalid command w/ args passed:")
        print(*args)

    def process_message(self, message):
        message_split = message.split()

        process_function = get_bot_commands(self).get(message_split[0], self.error_message)
        process_function(*message_split)

    def handle_tf2server_startup(pug_number):
        current_pug = self.pugs[pug_number]
        ec2_instance = current_pug.ec2_instance
        ec2_instance.await_instance_startup()

        # Start running commands for TF2 server / setup.
        current_pug.start_tf2_client()
        # blocks
        connected = current_pug.tf2_client.connect_to_server()
        if not connected:
            print("Unable to connect to TF2 server")
            # restart EC2 instance / grab new instance? TODO
            return

        current_pug.pug_state = pug.PugState.TF2_SERVER_ACTIVE
        # run TF2 RCON commands, etc

        # Wait here for TF2 SM plugin to send a message to a socket saying its setup? TODO
        print ("Done setup")
        
    def start_pug_command(self, pug_number):
        new_ec2_instance = self.ec2_interface.create_ec2_instance()
        if not new_ec2_instance:
            print("Unable to create ec2 instance, send help")
            return False

        # TODO should users have permissions to move ?
        self.create_pug_channels(pug_number)

        new_pug = pug.Pug(new_ec2_instance)
        self.pugs.append(pug_number, new_pug)

        # I don't think we particularly mind using normal threads here
        startup = threading.Thread(target=handle_tf2server_startup, args=(pug_number,))
        startup.start()

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
        pass
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

    def message_channel(self, *args):
        #print(self.mumble_client.channels.get_descendants(self.mumble_client.my_channel()))
        channel = self.mumble_client.channels.find_by_name("--------------- AMONG US ---------------")
        #movecmd = MoveCmd(self.mumble_client.users.myself_session, channel.get_id())
        #movecmd.lock.acquire()
        channel.move_in()
        #self.mumble_client.treat_command(movecmd)
        if channel:
            print(channel)
            #channel.send_text_message("pootis")
            #textmsg = TextMessage(self.mumble_client.users.myself_session, channel.get_id(), "pootis")
            #textmsg.lock.acquire()
            #self.mumble_client.treat_command(textmsg)

    def ping_users(self, *args):
        user_stats = pymumble.mumble_pb2.UserStats()
        user_stats.session = self.user_set_tmp[0]

        self.mumble_client.send_message(PYMUMBLE_MSG_TYPES_USERSTATS, user_stats)

    def toggle_mute(self, *args):
        # Mute / unmute the people in lobby / not playing
        pass

    def execute_rcon_command(self, *args):
        pug_number = args[0]
        command = args[1]
        for pug in self.pugs:
            if pug.pug_number == pug_id:
                pug_type.tf2_client.rcon_command(command)
                break
        else:
            print("Didn't execute command")
    

    def received_commands(self):
        while True:
            if self.mumble_client.commands.is_cmd():
                new_cmd = self.mumble_client.commands.pop_cmd()
                if new_cmd != None:
                    message = new_cmd.parameters["message"]
                    if message == "quit":
                        break
                    yield message

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Create a mumble bot for a designated server")
    parser.add_argument('--host', type=str, help="A string of the server IP/hostname", default='negasora.com')
    parser.add_argument('--port', type=int, help="An int of the servers port", default=64735)
    parser.add_argument('--name', type=str, help="Optional bot name", default='testbot')
    parser.add_argument('--pw', type=str, help="Optional password for server", default='')
    args = parser.parse_args()

    bot = MumbleBot(args.host, args.port, args.name, args.pw)
    #bot.start()
    loop = threading.Thread(target=bot.mumble_client.run())
    loop.start()
    #bot.mumble_client.run()
    for command in bot.received_commands():
        bot.process_message(command)
                
    bot.stop()