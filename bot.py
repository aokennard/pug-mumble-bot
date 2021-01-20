import pymumble_py3 as pymumble
from pymumble_py3.messages import TextMessage, MoveCmd
from pymumble_py3.constants import PYMUMBLE_MSG_TYPES_USERSTATS
from pymumble_py3.callbacks import PYMUMBLE_CLBK_TEXTMESSAGERECEIVED as RCV
from pymumble_py3.callbacks import PYMUMBLE_CLBK_USERCREATED as USR
from pymumble_py3.callbacks import PYMUMBLE_CLBK_ACLRECEIVED as ACL

from clients import EC2Interface
import auth
import pug as tf2pug
from config import config

import time
import threading
import random
import argparse
import requests
import pickle
import os

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
    <li>immune - Displays the medic immunity pool</li>
    <li>addimm <span style="color:red">user</span> - adds a player to the medic immunity pool manually</li>
    <li>takeimm <span style="color:red">user</span> - removes a player from the medic immunity pool manually</li>
    <li>dump <span style="color:red">pug-number</span> - Dumps the users in a pug channel to lobby, also cleans up remainder of that pugs info</li>   
    <li>rcon <span style="color:red">pug-number command</span> - uses 'rcon <span style="color:red">command</span>' for <span style="color:red">pug-number</span>'s tf2 server</li>    
    <li>quit - Turns off the bot</li>    
    <li>kick <span style="color:blue">[reason]</span> - Kicks a user</li>    
    <li>ban <span style="color:blue">[reason]</span> - Bans a user</li>
    </ul>
    For RCON commands, if you want to change to a map, you can use one of these shortcuts: snake, viaduct, clearcut, bagel, sunshine, process, villa, gully, metal
"""

APPEND_TF2_CFG_DATA = '''#!/bin/bash
sudo killall srcds_linux;
/bin/echo 'hey' > /home/ubuntu/hello
/bin/echo '' >> {path};
/bin/echo -n 'rcon_password {}; sv_password {};' >> {path};
cd /home/ubuntu/tf2server/hlserver;
./tf2.sh;
'''

CONNECT_STRING = 'connect {}; password {}' # '<a href="steam://connect/{}/{}">Connect to server</a>' # use DNS?

ROOT_NAME = config["root_name"]
LOBBY_NAME = config["lobby_name"]
VOLUNTEER_NAME = config["volunteer_name"]
CHILL_NAME = config["chill_name"]
PUG_FORMAT_NAME = config["pug_format_name"]
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

def save(func):
    def dec(*args, **kwargs):
        with open(config["bot_data_store_name"], 'wb') as f:
            pickle.dump(args[0].saveable, f)
        output = func(*args, **kwargs)
        with open(config["bot_data_store_name"], 'wb') as f:
            pickle.dump(args[0].saveable, f)
        return output
    return dec

class Saveable:
    def __init__(self, restore_name=config["bot_data_store_name"]):
        self.pugs = [None] * (config["max_pugs"] + 1)
        self.immunity_list = list()
        self.active_picking_pug = -1
        if os.path.isfile(restore_name):
            with open(restore_name, 'rb') as f:
                self.__dict__.update(pickle.load(f).__dict__)
            
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
    # TODO: on restarting in a mumble, analyze channels / try to use a file to regain pug states - test
    # TODO: channel perms
    # TODO: reap zombies
    # TODO: better style / helpers
    # TODO: statistics gathering on players, times for pugs / spin up etc
    def __init__(self, server_ip, server_port, nickname, password):
        self.mumble_client = pymumble.Mumble(server_ip, nickname, password=password, port=server_port, debug=False)
        
        self.saveable = Saveable()
        self.clients = [None] * (config["max_pugs"] + 1)

        self.pug_channels = dict()
        self.ec2_interface = EC2Interface(auth.get_aws_key_id(), auth.get_access_key())
        self.pug_bot_state = BotState.IDLE
        self.auto_roll = True
        self.active = True
        self.admins = []

        self.volunteer_channel = None
        self.lobby_channel = None
        self.root_channel = None
        self.chill_channel = None
        
        self.mumble_client.start()
        self.mumble_client.is_ready()

        self.setup_mumble_callbacks()
        self.create_base_channels()
        # populates self.admins
        # TODO possible race condition with user_id on joining?
        self.mumble_client.channels[0].get_acl()
        self.mumble_client.users.myself.move_in(self.root_channel["channel_id"])


    # Callbacks
    def setup_mumble_callbacks(self):
        self.mumble_client.callbacks.set_callback(RCV, self.message_received)
        self.mumble_client.callbacks.set_callback(ACL, self.on_ACL)
        self.mumble_client.callbacks.set_callback(USR, self.user_created)

    def on_ACL(self, event):
        for group in event.groups:
            if group.name == "admin":
                self.admins = [user for user in group.add]
                print(self.mumble_client.users)
                break

    def user_created(self, new_user):
        self.mumble_client.channels[0].get_acl()

    def message_received(self, proto_message):
        sender = proto_message.actor
            
        # https://github.com/azlux/pymumble/blob/pymumble_py3/pymumble_py3/mumble_pb2.py#L1060
        self.send_user_message(sender, "yo")
        uid = self.mumble_client.users[sender]["user_id"]

        if uid and uid in self.admins:
            self.process_message(proto_message.message, sender)
        else:
            self.send_user_message(sender, "You are not in the admin ACL group")

    # Channels
    def get_child_channel(self, parent_id, channel_name):
        channels = self.mumble_client.channels
        for child_channel in channels.get_childs(channels[parent_id]):
            if child_channel["name"] == channel_name:
                return child_channel
        raise pymumble.errors.UnknownChannelError(channel_name)

    def get_or_create_channel(self, channel, parent, temporary=True, children_only=False):
        channels = self.mumble_client.channels
        try:
            # TODO rewrite to use tree, duplicate BLU/RED
            new_channel = self.get_child_channel(parent, channel) if children_only else channels.find_by_name(channel)
            return new_channel
        except pymumble.errors.UnknownChannelError:
            print(parent, channel)
            channels.new_channel(parent, channel, temporary)
            # Required due to time to create channel - bot will crash as we can't find the channel after creating for a bit
            time.sleep(2)
            return channels.find_by_name(channel)

    def create_pug_channels(self, pug_number):
        new_pug_channel = self.get_or_create_channel(PUG_FORMAT_NAME.format(pug_number), self.lobby_channel["channel_id"], temporary=False)
        
        new_blu_channel = self.get_or_create_channel(BLU_CHANNEL_NAME, new_pug_channel["channel_id"], temporary=False, children_only=True)
        new_red_channel = self.get_or_create_channel(RED_CHANNEL_NAME, new_pug_channel["channel_id"], temporary=False, children_only=True)

        self.pug_channels[pug_number] = [new_pug_channel, new_blu_channel, new_red_channel]

    def create_base_channels(self):
        # bot exists in Root - TODO get perms
        self.root_channel = self.get_or_create_channel(ROOT_NAME, self.mumble_client.my_channel()["channel_id"], temporary=False)
        self.lobby_channel = self.get_or_create_channel(LOBBY_NAME, self.root_channel["channel_id"], temporary=False)
        self.volunteer_channel = self.get_or_create_channel(VOLUNTEER_NAME, self.lobby_channel["channel_id"], temporary=False)
        self.chill_channel = self.get_or_create_channel(CHILL_NAME, self.lobby_channel["channel_id"], temporary=False)

        for i in range(1, config["max_pugs"] + 1):
            self.create_pug_channels(i)

    def get_pug_users(self, pug_number):
        pug_data = self.pug_channels.get(pug_number)
        if pug_data:
            return pug_data[RED_CHANNEL_INDEX].get_users() + pug_data[BLU_CHANNEL_INDEX].get_users()

    def get_lobby_users(self, use_chill_room=True):
        lobby_users = self.lobby_channel.get_users() + self.volunteer_channel.get_users()
        if use_chill_room:
            lobby_users += set(self.chill_channel.get_users())
        return lobby_users

    def get_mumble_users(self, name_only=False):
        mumble_users = []
        for user in self.mumble_client.users.values():
            user = user["name"] if name_only else user
            mumble_users.append(user)
        return mumble_users

    # Pug data
    def get_new_pug_number(self):
        pug_number = 1
        max_pugs = config["max_pugs"]

        while pug_number <= max_pugs:
            if self.get_pug(pug_number) == None:
                return pug_number
            pug_number += 1

        return None

    def get_pug(self, pug_number):
        return self.saveable.pugs[pug_number]
    
    @save
    def set_pug(self, pug_number, value):
        self.saveable.pugs[pug_number] = value

    def get_active_picking_pug(self):
        return self.saveable.active_picking_pug
    
    @save
    def set_active_picking_pug(self, pug_number):
        self.saveable.active_picking_pug = pug_number

    def get_immunity_list(self):
        return self.saveable.immunity_list

    @save
    def set_immunity_list(self, new_list):
        self.saveable.immunity_list = new_list

    def remove_pug_data(self, pug_number):
        # pug_channel_root = self.pug_channels.get(pug_number)

        self.set_pug(pug_number, None)
        self.clients[pug_number] = None
        # pug_channel_root.remove()
        #del self.pug_channels[pug_number]

    def has_minimum_pug_players(self):
        return len(self.get_lobby_users(use_chill_room=False)) >= config["min_total_players"] 

    def checked_pug_number(self, args):
        if len(args) > 1:
            if type(args[0]) == int:
                return args[0]
            return int(args[0]) if args[0].isnumeric() else -1
        return -1

    # Messaging / commands
    def send_user_message(self, receiver, message):
        self.mumble_client.users[receiver].send_text_message(message)

    def error_message(self, *args):
        if isinstance(args[0], MumbleBot):
            args = args[1:]
        args_str = "(no args)"
        if len(args[:-1]) > 0:
            args_str = str(args[:-1])
        message = " ".join(["Invalid command, args passed:", args_str])
        print(message)
        self.send_user_message(args[-1], message)

    def get_bot_command(self, command):
        return cmd.get(command, self.error_message)     

    def process_message(self, message, sender):
        message_split = message.split()
        process_function = self.get_bot_command(message_split[0])
        process_function(self, *message_split[1:], sender)

    # Threading
    def medic_immunity_check(self):
        def immunity_callback():
            time.sleep(60 * 60 * config["medic_immunity_reset_hours"])
            # somewhat lazy, but this should account for when pugs die.
            # optimistically assumes after "medic_immunity_reset_hours" hours that we can say pugs are reset
            if len(self.get_lobby_users()) < config["min_total_players"]:
                self.reset_medic_immunity()

        threading.Thread(target=immunity_callback).start()          

    def handle_server_startup(self, pug_number, sender):
        current_pug = self.get_pug(pug_number)

        print("creating instance")
        ec2_instance = self.ec2_interface.create_ec2_instance()
        if ec2_instance == None:
            self.send_user_message(sender, "EC2 instance failed startup")
            return

        # TODO run this with SSM, as we will NEED to manually do an rcon/pass command, as we may have a reused instance
        time.sleep(2 * 60)
        ec2_instance.run_command(self.ec2_interface.ssm_client, "echo 'test' > /tmp/hellothere")
        ec2_instance.run_command(self.ec2_interface.ssm_client, APPEND_TF2_CFG_DATA.format(current_pug.rcon, current_pug.connect_pass, path=config["tf2_config_path"]))
        current_pug.set_ip(ec2_instance.get_ip())
        ec2_instance.await_instance_startup()

        # Start running commands for TF2 server / setup.
        print("tf2 server startup")
        current_pug.get_tf2_client()
        # blocks
        connected = current_pug.tf2_client.await_connect_to_server()
        if not connected:
            self.send_user_message(sender, "Unable to connect to TF2 server")
            # restart EC2 instance / grab new instance? TODO
            return

        current_pug.pug_state = tf2pug.PugState.TF2_SERVER_ACTIVE
        print("Run RCON stuff here")

        ip = requests.get('https://checkip.amazonaws.com').text.strip()
        current_pug.tf2_client.rcon_command("mbl_bot_address {}".format(ip))

        # Wait here for TF2 SM plugin to send a message to a socket saying its setup? TODO
        self.send_user_message(sender, "Done with TF2 server setup for pug {}".format(str(pug_number)))
        
    # Commands

    @cmd.new("state")
    def state(self, *args):
        self.send_user_message(args[-1], "Pug bot state: " + str(self.pug_bot_state))

    @cmd.new("quit")
    def stop(self, *args):
        self.mumble_client.stop()
        self.active = False
        os.remove(config["bot_data_store_name"])

    @cmd.new("reset")
    def reset_medic_immunity(self, *args):
        self.set_immunity_list(list())
        self.send_user_message(args[-1], "Reset medic immunity list")

    @cmd.new("immune")
    def display_medic_immunity(self, *args):
        sender = args[-1]
        self.send_user_message(sender, "Medic immunity: " + str(self.get_immunity_list()))

    @cmd.new("addimm")
    def add_medic_immunity(self, *args):
        user = args[0]
        if user not in self.get_mumble_users(name_only=True):
            self.send_user_message(args[-1], "User not in mumble, possibly a typo")
            return BotState.IDLE

        if user in self.get_immunity_list():
            self.send_user_message(args[-1], "User already in list")
            return BotState.IDLE
        
        self.send_user_message(args[-1], "User added to immunity list")
        self.set_immunity_list(self.get_immunity_list() + [user])
        return BotState.IDLE

    @cmd.new("takeimm")
    def remove_medic_immunity(self, *args):
        user = args[0]
        if user not in self.get_mumble_users(name_only=True):
            self.send_user_message(args[-1], "User not in mumble, possibly a typo")
            return BotState.IDLE

        if user not in self.get_immunity_list():
            self.send_user_message(args[-1], "User not in immunity list")
            return BotState.IDLE

        self.send_user_message(args[-1], "User removed from immunity list")

        filtered_list = self.get_immunity_list()
        filtered_list.remove(user)
        self.set_immunity_list(filtered_list)
        return BotState.IDLE

    @cmd.new("roll")
    def roll_medics(self, *args):
        medics = []
        volunteers = self.volunteer_channel.get_users()
        if len(volunteers) > 0:
            medics.extend(random.sample(volunteers, min(2, len(volunteers))))   

        medics_to_pick = 2 - len(medics) 

        lobby_players = self.lobby_channel.get_users()
        lobby_players_without_immunity = [player for player in lobby_players if player["name"] not in self.get_immunity_list()]
        
        if len(lobby_players_without_immunity) <= 0:
            if len(lobby_players) >= medics_to_pick:
                self.reset_medic_immunity()
            else:
                self.send_user_message(args[-1], "Fatal: no valid lobby players found")
                return BotState.INVALID

        if medics_to_pick > 0:
            medics.extend(random.sample(lobby_players, medics_to_pick))

        pug_channels = self.pug_channels[self.get_active_picking_pug()]
        red_channel, blu_channel = pug_channels[RED_CHANNEL_INDEX], pug_channels[BLU_CHANNEL_INDEX]

        pug_channels[RED_CHANNEL_INDEX].move_in(medics[0]["session"])
        pug_channels[BLU_CHANNEL_INDEX].move_in(medics[1]["session"])

        # channel move delay
        time.sleep(2)

        if len(self.volunteer_channel.get_users()) > 0:
            for volunteer in self.volunteer_channel.get_users():
                self.lobby_channel.move_in(volunteer["session"])

        # TODO account for subs / edge cases?
        self.set_immunity_list([player["name"] for player in medics if player["name"] not in self.get_immunity_list()] + self.get_immunity_list())
        self.pug_bot_state = BotState.MEDICS_PICKED

    @cmd.new("start")
    def start_pug_command(self, *args):
        sender = args[-1]

        if self.get_active_picking_pug() != -1:
            self.send_user_message(sender, "Unable to start new pug, pug already being picked")
            return BotState.IDLE

        if not self.has_minimum_pug_players():
            self.send_user_message(sender, "Unable to start new pug, not enough players")
            return BotState.IDLE

        self.pug_bot_state = BotState.STARTING
        pug_number = self.get_new_pug_number()
        if pug_number is None:
            self.send_user_message(sender, "Unable to start new pug, max limit of pugs reached")
            return BotState.IDLE

        self.set_active_picking_pug(pug_number)
        new_pug = tf2pug.Pug(pug_number)
        self.set_pug(pug_number, new_pug)

        # Spins up new EC2 instance, pre-imaged with TF2 server (CDK OR cli?)
        startup_thread = threading.Thread(target=self.handle_server_startup, args=(pug_number, sender))
        startup_thread.start()
        
        # Picking logic
        self.pug_bot_state = BotState.MEDIC_PICKING

        # After some amount of time / volunteer command called (whether by command or queue?), roll remainder medics.
        if self.auto_roll:
            time.sleep(config["autoroll_delay"])
            print("picking medics")
            self.roll_medics(sender)

            if self.pug_bot_state == BotState.INVALID:
                self.send_user_message(sender, "Invalid state from autorolling medics, ending draft {}".format(str(pug_number)))
                self.end_pug_command(pug_number, True, sender)
                return

        while self.pug_bot_state != BotState.MEDICS_PICKED:
            time.sleep(5) 
            print(self.pug_bot_state, "Waiting for med pics")
        print("Picking state now")

        # first or 2nd pick? random or rng?
        self.pug_bot_state = BotState.PICKING

        # picking phase (channel move - elevate permissions of people here / the channel, or just captains?)

        while True:
            if not self.get_pug(pug_number):
                print("Pug has been killed, ending picking loop")
                return BotState.IDLE 
            pugger_count = len(self.get_pug_users(pug_number))
            if pugger_count >= config["min_total_players"]:
                break
            time.sleep(5)
            print("Picking, current number of people in pug: ", pugger_count)
        print("Done picking")

        self.pug_bot_state = BotState.SENDING_INFO

        while True:
            if not self.get_pug(pug_number):
                print("Pug has been killed, ending picking loop")
                return BotState.IDLE 
            if new_pug.pug_state == tf2pug.PugState.TF2_SERVER_ACTIVE:
                break
            time.sleep(5)
            print("Waiting for active server")
        
        self.message_pug_channel(pug_number, CONNECT_STRING.format(new_pug.connect_ip, new_pug.connect_pass))
        
        self.pug_bot_state = BotState.SENT_INFO

        # Anything else here?
        self.set_active_picking_pug(-1)
        return BotState.IDLE 

    @cmd.new("help")
    def help_message(self, *args):
        sender = args[-1]
        self.mumble_client.users[sender].send_text_message(HELP_STRING)
        print(self.saveable.pugs)

    @cmd.new("dump")
    def dump_channel_and_cleanup(self, *args):
        pug_number = self.checked_pug_number(args)
        if pug_number == -1:
            self.send_user_message(sender, "Invalid arguments")
            return BotState.IDLE

        sender = args[-1]     

        users = self.get_pug_users(pug_number)
        if not users:
            self.send_user_message(sender, "Pug channel not found, cannot move out / remove")
            return BotState.IDLE

        for user in users:
            self.lobby_channel.move_in(user["session"])
        
        # clears relevant pugN data for mumble.
        self.remove_pug_data(pug_number)

        # Starts a callback which may clear the medic immunity set
        self.medic_immunity_check()

        self.send_user_message(sender, "Ended pug {}".format(str(pug_number)))

        return BotState.IDLE

    # This is likely received as a command from the TF2 SM plugin.
    @cmd.new("end")
    def end_pug_command(self, *args):
        self.pug_bot_state = BotState.ENDING_PUG
        sender = args[-1] # TODO special logic for SM plugin sender?
        pug_number = self.checked_pug_number(args)
        if pug_number == -1:
            self.send_user_message(sender, "Invalid arguments")
            return BotState.IDLE

        override = False
        if len(args) > 2:
            override = int(args[1]) if (type(args[1]) == str and args[1].isnumeric()) or type(args[1]) == int else 0

        # tells EC2 instance to spin down (or wait a few minutes, monitor num people in mumble / lobby to see if pugs still going)
        pug = self.get_pug(pug_number)
        if not pug:
            self.send_user_message(sender, "Pug not found, cannot end")
            return BotState.IDLE

        print("Spinning down pug server")
        # using mumble monitoring, may not spin down 
        spindown_thread = threading.Thread(target=self.ec2_interface.spin_down_instance, args=(pug.ec2_instance,), kwargs={'use_mumble_monitoring':config["use_mumble_monitoring"], 'mumble_client':self.mumble_client})
        spindown_thread.start()

        # Dumps people to lobby channel, but we may need to lock lobby when picking.
        if not override: 
            time.sleep(config["end_pug_delay"])
            if self.get_active_picking_pug() != -1:
                self.send_user_message(sender, "Picking currently happening, not dumping pug {}".format(str(pug_number)))
                return BotState.IDLE
        
        self.dump_channel_and_cleanup(pug_number, sender)

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
    def message_pug_channel(self, *args):
        channels = self.pug_channels.get(args[0])

        if channels:
            for channel in channels:
                channel.send_text_message(args[1])

    @cmd.new("ping")
    def ping_users(self, *args):
        user_stats = pymumble.mumble_pb2.UserStats()
        user_stats.session = self.user_set_tmp[0]

        self.mumble_client.send_message(PYMUMBLE_MSG_TYPES_USERSTATS, user_stats)

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
        client = self.clients.get(pug_number)
        if client:
            client.rcon_command(command)
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
