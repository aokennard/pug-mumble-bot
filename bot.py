import pymumble_py3 as pymumble
from pymumble_py3.messages import TextMessage, MoveCmd
from pymumble_py3.constants import PYMUMBLE_MSG_TYPES_USERSTATS
from pymumble_py3.callbacks import PYMUMBLE_CLBK_TEXTMESSAGERECEIVED as RCV
from pymumble_py3.callbacks import PYMUMBLE_CLBK_CHANNELCREATED as CC

from clients import EC2Interface
import auth
import pug as tf2pug
from config import config

import time
import threading
import random
import argparse

HELP_STRING = """Bot commands:<br>
    <span style="color:red">Red text</span> indicates required argument, <span style="color:blue">blue text</span> is optional
    <ul>
    <li>mute - Mutes all users in lobby, volunteer, and chill channel</li>
    <li>unmute - Unmutes all users in lobby, volunteer, and chill channel</li>
    <li>help - Displays this help message</li>
    <li>start - Starts pug: creates channels, acquires server, sets up TF2 server, medic rolling logic, info sending</li>
    <li>end <span style="color:red">pug-number</span> <span style="color:blue">[override: 0 or 1]</span>
     - Ends pug: removes channels (with a delay, unless the override is 1), tells server to shut down (possibly), moves users to lobby</li>
    <li>roll - Uses the active pug kept by the bot + volunteers in the channel to roll medics and move them into proper create_base_channels</li>
    <li>reset - Manually resets the medic immunity pool</li>
    <li>dump <span style="color:red">pug-number</span> - Dumps the users in a pug channel to lobby, also cleans up remainder of that pugs info</li>   
    <li>rcon <span style="color:red">pug-number command</span> - uses 'rcon <span style="color:red">command</span>' for <span style="color:red">pug-number</span>'s tf2 server</li>    
    <li>quit - Turns off the bot</li>    
    <li>kick <span style="color:blue">[reason]</span> - Kicks a user</li>    
    <li>ban <span style="color:blue">[reason]</span> - Bans a user</li>
    </ul>
"""

ROOT_NAME = config["root_name"]
LOBBY_NAME = config["lobby_name"]
VOLUNTEER_NAME = config["volunteer_name"]
CHILL_NAME = config["chill_name"]
PUG_FORMAT_NAME = config["chill_name"]
BLU_CHANNEL_NAME = config["BLU"]
RED_CHANNEL_NAME = config["RED"]

PUG_ROOT_INDEX = 0
BLU_CHANNEL_INDEX = 1
RED_CHANNEL_INDEX = 2

class BotState:
    INVALID = -1
    IDLE = 0
    STARTING = 1
    MEDIC_PICKING = 2
    MEDICS_PICKED = 3
    PICKING = 4
    SENDING_INFO = 5 
    SENT_INFO = 6 
    ENDING_PUG = 7 

class CommandRegister(object):
    def __init__(self):
        self._commands = {}
    
    def __getitem__(self, item):
        return self._commands[item]
    
    def get(self, item, default=None):
        return self._commands.get(item, default)

    def new(self, command):
        def wrapper(f):
            self._commands[command] = f
            return f
        return wrapper

cmd = CommandRegister()

class MumbleBot:
    def __init__(self, server_ip, server_port, nickname, password):
        self.mumble_client = pymumble.Mumble(server_ip, nickname, password=password, port=server_port, debug=False)
        self.pugs = [None] * (config["max_pugs"] + 1)
        self.pug_channels = dict()
        self.user_set_tmp = []
        self.ec2_interface = EC2Interface(auth.get_aws_key_id(), auth.get_access_key())
        self.pug_bot_state = BotState.IDLE
        self.immunity_set = set()
        self.volunteers = set()
        self.active_picking_pug = -1
        self.auto_roll = True
        self.active = True

        self.volunteer_channel = None
        self.lobby_channel = None
        self.root_channel = None
        self.chill_channel = None
        
        self.mumble_client.start()
        self.mumble_client.is_ready()

        #loop = threading.Thread(target=self.mumble_client.run)
        #loop.start()

        self.setup_mumble_callbacks()
        self.create_base_channels()

    def send_user_message(self, receiver, message):
        self.mumble_client.users[receiver].send_text_message(message)

    def error_message(self, *args):
        message = " ".join(["Invalid command, args passed:", *args[:-1]])
        print(message)
        self.send_user_message(args[-1], message)

    def get_bot_command(self, command):
        return cmd.get(command, self.error_message)
        '''return {"kick" : self.kick_user,
            "ban"  : self.ban_user,
            "quit" : self.stop,
            "mesg" : self.message_channel,
            "ping" : self.ping_users,
            "roll" : self.roll_medics,
            "mute" : self.mute_lobby,
            "unmute" : self.unmute_lobby,
            "start" : self.start_pug_command,
            "reset" : self.reset_medic_immunity,
            "help" : self.help_message,
            "dump" : self.dump_channel_and_cleanup,
            "rcon" : self.execute_rcon_command,
            "end" : self.end_pug_command}.get(command, self.error_message)'''
        
    def volunteer_check(self, sender):
        # actor instead?
        if sender in self.mumble_client.users and self.volunteer_channel and self.mumble_client.users[sender]["channel_id"] == self.volunteer_channel["channel_id"]:
            self.volunteers.add(sender)
        elif sender in self.volunteers:
            self.volunteers.remove(sender)

    # movecmd?
    def message_received(self, proto_message):
        sender = proto_message.actor
        
        self.volunteer_check(sender)
    
        # https://github.com/azlux/pymumble/blob/pymumble_py3/pymumble_py3/mumble_pb2.py#L1060
        self.user_set_tmp.append(sender)
        self.send_user_message(sender, "yo")
        self.process_message(proto_message.message, sender)
        
        #self.mumble_client.commands.new_cmd(TextMessage(proto_message.actor, proto_message.channel_id, proto_message.message))
    
    def process_message(self, message, sender):
        message_split = message.split()

        process_function = self.get_bot_command(message_split[0])
        self.pug_bot_state = process_function(self, *message_split[1:], sender)

    # TODO bot still crashes after creating 1 channel at a time.
    def get_or_create_channel(self, channel, parent, temporary=True):
        channels = self.mumble_client.channels
        try:
            new_channel = channels.find_by_name(channel)
            return new_channel
        except pymumble.errors.UnknownChannelError:
            print(parent, channel)
            channels.new_channel(parent, channel, temporary)
            return channels.find_by_name(channel)

    def create_base_channels(self):
        # bot exists in Root - TODO get perms
        self.root_channel = self.get_or_create_channel(ROOT_NAME, self.mumble_client.my_channel()["channel_id"], temporary=False)
        self.lobby_channel = self.get_or_create_channel(LOBBY_NAME, self.root_channel["channel_id"], temporary=False)
        self.volunteer_channel = self.get_or_create_channel(VOLUNTEER_NAME, self.lobby_channel["channel_id"], temporary=False)
        self.chill_channel = self.get_or_create_channel(CHILL_NAME, self.lobby_channel["channel_id"], temporary=False)

    def create_channel(self, arg):
        print(arg)

    def setup_mumble_callbacks(self):
        self.mumble_client.callbacks.set_callback(RCV, self.message_received)
        self.mumble_client.callbacks.set_callback(CC, self.create_channel)

    @cmd.new("help")
    def help_message(self, *args):
        sender = args[-1]
        self.mumble_client.users[sender].send_text_message(HELP_STRING)
        
    def stop(self, *args):
        self.mumble_client.stop()
        self.active = False

    def get_new_pug_number(self):
        pug_number = 1
        max_pugs = config["max_pugs"]

        while pug_number <= max_pugs:
            if self.get_pug(pug_number) != None:
                return pug_number
            pug_number += 1

        return None

    def get_pug(self, pug_number):
        return self.pugs[pug_number] if pug_number != -1 else None

    def remove_pug_data(self, pug_number):
        pug_channel_root = self.pug_channels.get(pug_number)

        self.pugs[pug_number] = None
        pug_channel_root.remove()
        del self.pug_channels[pug_number]
 
    def create_pug_channels(self, pug_number):
        new_pug_channel = self.get_or_create_channel(PUG_FORMAT_NAME.format(pug_number), self.lobby_channel)

        new_blu_channel = self.get_or_create_channel(BLU_CHANNEL_NAME, new_pug_channel)
        new_red_channel = self.get_or_create_channel(RED_CHANNEL_NAME, new_pug_channel)

        self.pug_channels[PUG_FORMAT_NAME.format(pug_number)] = [new_pug_channel, new_blu_channel, new_red_channel]

    @cmd.new("reset")
    def reset_medic_immunity(self, *args):
        self.immunity_set = set()
    
    @cmd.new("roll")
    def roll_medics(self, *args):
        medics = []
        volunteers = set(self.volunteer_channel.get_users())
        if len(volunteers) > 0:
            medics.extend(random.sample(volunteers, max(2, len(volunteers))))   

        medics_to_pick = 2 - len(medics) 

        lobby_players = set(self.lobby_channel.get_users())
        lobby_players_without_immunity = lobby_players - self.immunity_set
        
        if len(lobby_players_without_immunity) <= 0:
            if len(lobby_players) >= medics_to_pick:
                self.reset_medic_immunity()
            else:
                self.send_user_message(args[-1], "Fatal: no valid lobby players found")
                return BotState.INVALID

        if medics_to_pick > 0:
            medics.extend(random.sample(lobby_players, medics_to_pick))

        pug_channels = self.pug_channels[PUG_FORMAT_NAME.format(self.active_picking_pug)]
        red_channel_id, blu_channel_id = pug_channels[RED_CHANNEL_INDEX]["channel_id"], pug_channels[BLU_CHANNEL_INDEX]["channel_id"]

        medics[0].move_in(red_channel_id)
        medics[1].move_in(blu_channel_id)

        # TODO account for subs / edge cases?
        self.immunity_set.update(medics)
        return BotState.MEDICS_PICKED

    def medic_immunity_check(self):
        def immunity_callback():
            time.sleep(60 * 60 * config["medic_immunity_reset_hours"])
            # somewhat lazy, but this should account for when pugs die.
            # optimistically assumes after "medic_immunity_reset_hours" hours that we can say pugs are reset
            if len(self.get_lobby_users()) < config["min_total_players"]:
                self.reset_medic_immunity()

        threading.Thread(target=immunity_callback).start()          

    def handle_tf2server_startup(self, pug_number, sender):
        current_pug = self.get_pug(pug_number)
        ec2_instance = current_pug.ec2_instance
        ec2_instance.await_instance_startup()

        # Start running commands for TF2 server / setup.
        current_pug.start_tf2_client()
        # blocks
        connected = current_pug.tf2_client.connect_to_server()
        if not connected:
            self.send_user_message(sender, "Unable to connect to TF2 server")
            # restart EC2 instance / grab new instance? TODO
            return

        current_pug.pug_state = tf2pug.PugState.TF2_SERVER_ACTIVE
        # run TF2 RCON commands, etc

        # Wait here for TF2 SM plugin to send a message to a socket saying its setup? TODO
        self.send_user_message(sender, "Done with TF2 server setup for pug {}".format(str(pug_number)))
        
    def can_start_pug(self):
        return len(self.get_lobby_users(use_chill_room=False)) >= config["min_total_players"]

    @cmd.new("start")
    def start_pug_command(self, *args):
        self.pug_bot_state = BotState.STARTING
        sender = args[-1]

        if not self.can_start_pug():
            return BotState.IDLE

        pug_number = self.get_new_pug_number()
        if pug_number is None:
            self.send_user_message(sender, "Unable to start new pug, max limit reached")
            return BotState.IDLE

        self.active_picking_pug = pug_number
        # Spins up new EC2 instance, pre-imaged with TF2 server (CDK OR cli?)
        new_ec2_instance = self.ec2_interface.create_ec2_instance()
        if not new_ec2_instance:
            self.send_user_message(sender, "Unable to create ec2 instance, going to idle")
            return BotState.IDLE

        # TODO should users have permissions to move ?
        self.create_pug_channels(pug_number)

        new_pug = tf2pug.Pug(new_ec2_instance)
        self.pugs[pug_number] = new_pug

        startup_thread = threading.Thread(target=self.handle_tf2server_startup, args=(pug_number, sender))
        startup_thread.start()
        
        # Picking logic
        self.pug_bot_state = BotState.MEDIC_PICKING

        # After some amount of time / volunteer command called (whether by command or queue?), roll remainder medics.
        if self.auto_roll:
            time.sleep(config["autoroll_delay"])
            self.process_message("roll")

            if self.pug_bot_state == BotState.INVALID:
                self.send_user_message(sender, "Invalid state from autorolling medics, ending draft {}".format(str(pug_number)))
                self.end_pug_command(pug_number, True, sender)
                return BotState.IDLE
        else:
            while self.pug_bot_state != BotState.MEDICS_PICKED:
                time.sleep(5) 

        # first or 2nd pick? random or rng?
        self.pug_bot_state = BotState.PICKING

        # picking phase (command, or by channel move?)

        self.pug_bot_state = BotState.SENDING_INFO
        # currently: pin connect to Pug <n> comment / description - once tf2 server starts up.
        # could wait unilt 12 people in channel and then send to all users so not any1 can join?
        
        self.pug_bot_state = BotState.SENT_INFO

        # Anything else here?
        self.active_picking_pug = -1
        return BotState.IDLE      

    # This is likely received as a command from the TF2 SM plugin.
    @cmd.new("end")
    def end_pug_command(self, *args):
        self.pug_bot_state = BotState.ENDING_PUG
        pug_number = args[0] if args[0].isnumeric() else -1
        override = False
        if len(args) > 2 and args[1].isnumeric():
            override = int(args[1])
            
        sender = args[-1]

        # tells EC2 instance to spin down (or wait a few minutes, monitor num people in mumble / lobby to see if pugs still going)
        pug = self.get_pug(pug_number)
        if not pug:
            self.send_user_message(sender, "Pug not found, cannot end")
            return BotState.IDLE

        # using mumble monitoring, may not spin down 
        spindown_thread = threading.Thread(target=self.ec2_interface.spin_down_instance, args=(pug.ec2_instance,), kwargs={'use_mumble_monitoring':True, 'mumble_client':self.mumble_client})
        spindown_thread.start()

        # Dumps people to lobby channel, but we may need to lock lobby when picking.
        # TODO lock on lobbies / ending pugs if existing pug picking, temp lobbies?
        if not override: 
            time.sleep(config["end_pug_delay"])
            if self.active_picking_pug != -1:
                self.send_user_message(sender, "Picking currently happening, not dumping pug {}".format(str(pug_number)))
                return BotState.IDLE
        
        self.dump_channel_and_cleanup(pug_number, sender)

        return BotState.IDLE

    @cmd.new("dump")
    def dump_channel_and_cleanup(self, *args):
        pug_number = args[0]
        sender = args[-1]
        pug_channel_data = self.pug_channels.get(pug_number)
        
        if not pug_channel_data:
            self.send_user_message(sender, "Pug channel not found, cannot move out / remove")
            return BotState.IDLE

        users = pug_channel_data[PUG_ROOT_INDEX].get_users()
        for user in users:
            self.lobby_channel.move_in(user["session"])
        
        # Explicitly deletes the pugN channels, clears relevant pugN data for mumble.
        self.remove_pug_data(pug_number)

        # Starts a callback which may clear the medic immunity set
        self.medic_immunity_check()

        self.send_user_message(sender, "Ended pug {}".format(str(pug_number)))

        return BotState.IDLE

    @cmd.new("kick") 
    def kick_user(self, *args):
        user = args[0]
        reason = "Kicked from server"

        if len(args) > 1:
            reason = args[1]

        user.kick(reason)

    @cmd.new("ban")
    def ban_user(self, *args):
        user = args[0]
        reason = "Banned from server"

        if len(args) > 1:
            reason = args[1]

        user.ban(reason)

    @cmd.new("mesg")
    def message_channel(self, *args):
        channel = self.mumble_client.channels.find_by_name(args[0])
        channel.move_in()
        if channel:
            print(channel)
            #channel.send_text_message("pootis")
            #textmsg = TextMessage(self.mumble_client.users.myself_session, channel.get_id(), "pootis")
            #textmsg.lock.acquire()
            #self.mumble_client.treat_command(textmsg)

    @cmd.new("ping")
    def ping_users(self, *args):
        user_stats = pymumble.mumble_pb2.UserStats()
        user_stats.session = self.user_set_tmp[0]

        self.mumble_client.send_message(PYMUMBLE_MSG_TYPES_USERSTATS, user_stats)

    def get_lobby_users(self, use_chill_room=True):
        lobby_users = set(self.lobby_channel.get_users()) | set(self.volunteer_channel.get_users())
        if use_chill_room:
            lobby_users |= set(self.chill_channel.get_users())
        return lobby_users

    @cmd.new("mute")
    def mute_lobby(self, *args):
        lobby_users = self.get_lobby_users()
        for user in lobby_users:
            user.mute()

    @cmd.new("unmute")
    def unmute_lobby(self, *args):
        lobby_users = self.get_lobby_users()
        for user in lobby_users:
            user.unmute()

    @cmd.new("rcon")  
    def execute_rcon_command(self, *args):
        pug_number = args[0]
        command = args[1]
        pug = self.get_pug(pug_number)
        if pug:
            pug.tf2_client.rcon_command(command)
        else:
            self.send_user_message(args[-1], "Didn't execute command for pug {}".format(str(pug_number)))
    

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Create a mumble bot for a designated server")
    parser.add_argument('--host', type=str, help="A string of the server IP/hostname", default='3.23.68.159')
    parser.add_argument('--port', type=int, help="An int of the servers port", default=64738)
    parser.add_argument('--name', type=str, help="Optional bot name", default='testbot')
    parser.add_argument('--pw', type=str, help="Optional password for server", default='')
    args = parser.parse_args()

    bot = MumbleBot(args.host, args.port, args.name, args.pw)

    while True:
        if not bot.active:
            break
        time.sleep(3)
