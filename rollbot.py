import pymumble_py3 as pymumble
from pymumble_py3.callbacks import PYMUMBLE_CLBK_TEXTMESSAGERECEIVED as RCV
import argparse
import time
import signal
import sys
import random

bot = None
LOBBY_NAME = "ADD UP HERE"

def kill_bot(signal, frame):
    print("Killing bot!")
    bot.client.stop()
    bot.active = False
    sys.exit(0)

class RollBot:
    def __init__(self, host, port, name, pw):
        self.client = pymumble.Mumble(host, name, password=pw, port=port, debug=False)

        self.client.start()
        self.client.is_ready()

        self.active = True
        self.immune_players = []
        self.lobby = self.client.channels.find_by_name(LOBBY_NAME)
        self.client.callbacks.set_callback(RCV, self.message_received)
        self.client.users.myself.comment("commands: roll [number], add [name] (add to immunity list), rm [name] (remove from list), clearimm (fully clear list)")
    
    def send_message(self, sender, message):
        self.client.users[sender].send_text_message(message)
    
    def get_child_channel(self, parent_id, channel_name):
        channels = self.client.channels
        for child_channel in channels.get_childs(channels[parent_id]):
            if child_channel["name"] == channel_name:
                return child_channel
        raise pymumble.errors.UnknownChannelError(channel_name)

    def message_received(self, proto_msg):
        sender = proto_msg.actor
        message = proto_msg.message.split()
        if "roll" == message[0]:
            self.roll(int(message[1]), sender)
        elif "add" == message[0]:
            self.addimm(message[1], sender)
        elif "rm" == message[0]:
            self.rmimm(message[1], sender)
        elif "clear" == message[0]:
            self.clearimm(sender)
        elif "dc" == message[0]:
            self.dump(message[1], sender)
        else:
            self.send_message(sender, "Incorrect command")

    def get_pug_users(self, pug):
        pug_channel = self.client.channels.find_by_name("Pug {}".format(pug))
        blu_channel = self.get_child_channel(pug_channel["channel_id"], "blu")
        red_channel = self.get_child_channel(pug_channel["channel_id"], "red")
        return blu_channel.get_users() + red_channel.get_users()

    def dump(self, pug, sender):
        users = self.get_pug_users(pug)
        if users == None:
            self.send_user_message(sender, "Pug channel / users not found, cannot move out / remove")
            return
        for user in users:
            self.lobby.move_in(user["session"])

    def roll(self, n_players, sender):
        lobby_players = self.lobby.get_users()
        lobby_players_without_immunity = [player for player in lobby_players if player["name"] not in self.immune_players]
        if len(lobby_players_without_immunity) < n_players:
            self.send_message(sender, "Unable to roll, not enough players")
            return

        medics = list(map(lambda x: x["name"], random.sample(lobby_players_without_immunity, n_players)))
        self.send_message(sender, "Medics: {}".format(medics))
        for medic in medics:
            self.addimm(medic, sender)

    def addimm(self, name, sender):
        self.immune_players.append(name)
        self.send_message(sender, "Added {} to immune list".format(name))

    def rmimm(self, name, sender):
        self.immune_players.remove(name)
        self.send_message(sender, "Removed {} from immune list".format(name))

    def clearimm(self, sender):
        self.immune_players = []
        self.send_message(sender, "Cleared immune list")

if __name__ == '__main__':
    signal.signal(signal.SIGINT, kill_bot)
    parser = argparse.ArgumentParser(description="Create a mumble bot for a designated server")
    parser.add_argument('--host', type=str, help="A string of the server IP/hostname", default='outcheapugs.cheapmumble.com')
    parser.add_argument('--port', type=int, help="An int of the servers port", default=2283)
    parser.add_argument('--name', type=str, help="Optional bot name", default='rollbot (comment)')
    parser.add_argument('--pw', type=str, help="Optional password for server", default='')
    args = parser.parse_args()

    bot = RollBot(args.host, args.port, args.name, args.pw)

    while True:
        time.sleep(5)