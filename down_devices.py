#!/usr/bin/python

###################################
# Filename:       down_devices.py #
# Author:         Chris Snyder    #
# Created:        08/29/2017      #
# Last Modified:  09/25/2017      #
# Python Version: 2.6.6           # 
###################################

import curses
import os #TODO Replace OS Calls w/ Subprocess (Agnostic)

from datetime import datetime, timedelta
from multiprocessing.dummy import Pool as ThreadPool

#Software Version (Major.Minor.Patch)
__version__ = '1.2.0'

HOSTFILE     = '/etc/hosts'
PING_CMD     = 'ping -c1 -w2 %hostname > /dev/null 2>&1'
LOG_TIME_FMT = '[%H:%M:%SZ]'

PING_INTERVAL = 300 #Seconds
POOL_SIZE     = 128 #Threads

RECENT_EVENT_THOLD = 30 #Minutes

MAX_LOG_SIZE = 255

IGNORE_HOSTS = [
 'att',
 '-ex',
 'hsrp',
 'idrac',
 'localhost',
 '-mp',
 'nsrp',
 'vrrp'
]

SORT_OPTIONS = [
 'MOST RECENT',
 'ALPHABETICALLY'
]

HELP_MENU_ITEMS = [
 ('PGUP', 'Previous Page'),
 ('PGDN', 'Next Page'),
 ('HOME', 'First Page'),
 (' END', 'Last Page'),
 ('ENTR', 'Search Nodes'),
 (' INS', 'Cycle Sort'),
 ('   C', 'Print Count'),
 ('   P', 'Manual Ping'),
 ('   Q', 'Quit')
]

KEY_NEWLINE = ord('\n')
KEY_RETURN  = ord('\r')

KEY_ENTER = [
 curses.KEY_ENTER,
 KEY_NEWLINE,
 KEY_RETURN
]

class Node(object):
 STATE_UP   = 'UP'
 STATE_DOWN = 'DOWN'

 def __init__(self, hostname, ip_addr):
  self.hostname = hostname
  self.ip_addr = ip_addr

  self.set_state(Node.STATE_DOWN)

 #Returns True if State Changed
 def set_state(self, new_state):
  #EAFP
  try:
   old_state = self.state
  except AttributeError:
   old_state = None

  self.state = new_state

  if new_state != old_state:
   self.last_state_change = datetime.now()

   return True
  else:
   return False

 #Implement EQ/HASH for Set Support
 def __eq__(self, other):
  return self.hostname == other.hostname

 def __hash__(self):
  return hash(self.hostname)

 def __str__(self):
  return self.hostname + ' (' + self.ip_addr + ')'

class App(object):
 ROOT_SCREEN = 0
 UPPR_SCREEN = 1
 LOWR_SCREEN = 2
 HELP_SCREEN = 3

 def __init__(self, std_screen):
  #Set Curses Options
  curses.use_default_colors()
  curses.curs_set(0) #Hide Cursor

  #Create (Sub)Screens
  self.screens = self.initialize_screens(std_screen)

  #Upper Screen
  self.nodes       = list()
  self.dirty_nodes = list()
  self.view_nodes  = list()

  self.last_ping_time = datetime(1970, 1, 1, 0, 0, 0)

  self.current_filter = '' #None
  self.current_sort   = 0  #Most Recent

  self.current_page = 1
  self.total_pages  = 1

  #Lower Screen
  self.logs = list()

  #Help Screen
  self.show_help = True

  #App Entry Point
  self.run()

 def run(self):
  self.run_ping = True
  self.running  = True

  self.start_time = datetime.now()

  self.log('STARTUP: PING_INTERVAL is ' + str(PING_INTERVAL) + ' seconds.')

  self.load_hostfile()

  while(self.running):
   self.handle_keys()

   #Erase Each (Sub)Screen
   self.erase_screens()

   #Ping on Current Loop?
   next_ping_time = self.last_ping_time + timedelta(seconds=PING_INTERVAL)

   if datetime.now() > next_ping_time:
    self.run_ping = True

   if self.run_ping:
    #Reset Current Page
    self.current_page = 1

    self.update_screens()
    self.draw_screens()
    self.ping_all()

    self.run_ping = False

    continue

   #Clear Node View
   del self.view_nodes[:]

   #Update Node View
   for node in self.nodes:
    if node.state == Node.STATE_DOWN:
     if self.current_filter.lower() in node.hostname.lower():
      self.view_nodes.append(node)

   #Sort Nodes Alphabetically, Most Recent, Etc...
   self.view_nodes = self.sort_nodes(self.view_nodes)

   #Determine Page Size and Number of Pages
   uppr_screen = self.screens[App.UPPR_SCREEN]
   uppr_lines, uppr_cols  = uppr_screen.getmaxyx()

   nodes_per_page = uppr_lines - 2 #Account for Borders

   self.total_pages = len(self.view_nodes) / nodes_per_page

   if (self.total_pages * nodes_per_page) < len(self.view_nodes):
    self.total_pages = self.total_pages + 1

   self.total_pages = max(self.total_pages, 1)

   #Adjust View Based on Current Page
   view_min = (self.current_page - 1) * nodes_per_page
   view_max = self.current_page * nodes_per_page

   self.view_nodes = self.view_nodes[view_min:view_max]

   #Log Node State Changes
   for node in self.dirty_nodes:
    self.log(str(node) + ' is ' + node.state + '.')
    self.dirty_nodes.remove(node)

   #Update Each (Sub)Screen
   self.update_screens()

   #Draw Each (Sub)Screen
   self.draw_screens()

 def handle_keys(self):
  #Get Input from the Root Screen
  #While Non-Blocking, GETCH() Returns -1
  key = self.screens[App.ROOT_SCREEN].getch()

  if key == ord('h'):
   self.show_help = not self.show_help
  elif key == ord('q'):
   self.running = False
  elif key == ord('p'):
   self.run_ping = True
  elif key == ord('c'):
   down_str = str(self.get_down_count()) + ' / ' + str(len(self.nodes))

   self.log('COUNT: ' + down_str + ' DOWN.')
  elif key == curses.KEY_IC:
   self.current_sort = (self.current_sort + 1) % len(SORT_OPTIONS)
   self.current_page = 1
  elif key in KEY_ENTER:
   self.current_filter = self.getstr()
   self.current_page = 1
  elif key == curses.KEY_END:
   self.current_page = self.total_pages
  elif key == curses.KEY_HOME:
   self.current_page = 1
  elif key == curses.KEY_NPAGE:
   self.current_page = min(self.current_page + 1, self.total_pages)
  elif key == curses.KEY_PPAGE:
   self.current_page = max(self.current_page - 1, 1)

 def initialize_screens(self, std_screen):
  std_screen.nodelay(1) #Non-Blocking

  help_lines = len(HELP_MENU_ITEMS) + 2

  max_short  = len(max([item[0] for item in HELP_MENU_ITEMS], key=len))
  max_long   = len(max([item[1] for item in HELP_MENU_ITEMS], key=len))

  help_cols  = max_short + len(' - ') + max_long + 2
  help_y     = 1
  help_x     = 80 - help_cols - 2

  return [
   std_screen,
   std_screen.subwin(16, 80, 0, 0),
   std_screen.subwin(8, 80, 16, 0),
   std_screen.subwin(help_lines, help_cols, help_y, help_x)
  ]

 def erase_screens(self):
  for screen in self.screens:
   screen.erase()  

 def update_screens(self):
  #Update UPPR_SCREEN
  uppr_screen = self.screens[App.UPPR_SCREEN]

  uppr_screen.border(0)
  self.addstr(uppr_screen, 0, 1, '/       /')
  self.addstr(uppr_screen, 0, 3, 'NODES', curses.A_BOLD)

  filter_str = '\'' + self.current_filter + '\''
  sort_str   = SORT_OPTIONS[self.current_sort]

  self.addstr(uppr_screen, 0, 11, sort_str, curses.A_REVERSE)

  if self.current_filter:
   self.addstr(uppr_screen, 0, 12 + len(sort_str), filter_str, curses.A_REVERSE)

  help_str = '[H - %show_hide Help]'

  if self.show_help:
   help_str = help_str.replace('%show_hide', 'Hide')
  else:
   help_str = help_str.replace('%show_hide', 'Show')

  self.addstr(uppr_screen, 0, 80 - len(help_str) - 2, help_str)

  if self.run_ping:
   self.addstr(uppr_screen, 1, 1, 'Standby, Pinging Nodes...')
  else:
   for index, node in enumerate(self.view_nodes):
    mode = curses.A_NORMAL

    recent_event_cutoff = datetime.now() - timedelta(minutes=RECENT_EVENT_THOLD)

    min_time = self.start_time - timedelta(seconds=PING_INTERVAL)
    max_time = self.start_time + timedelta(seconds=PING_INTERVAL)

    if not (min_time <= node.last_state_change <= max_time):
     if node.last_state_change > recent_event_cutoff:
      mode = curses.A_UNDERLINE

    self.addstr(uppr_screen, index + 1, 1, str(node), mode)

  page_str = str(self.current_page) + ' / ' + str(self.total_pages)

  self.addstr(uppr_screen, 15, 80 - len(page_str) - 2, page_str)

  #Update LOWR_SCREEN
  lowr_screen = self.screens[App.LOWR_SCREEN]

  lowr_screen.border(0)
  self.addstr(lowr_screen, 0, 1, '/      /')
  self.addstr(lowr_screen, 0, 3, 'LOGS', curses.A_BOLD)

  for index, entry in enumerate(self.logs[-6:]):
   self.addstr(lowr_screen, index + 1, 1, entry)

  version_str = 'v' + __version__
  version_len = len(version_str)

  self.addstr(lowr_screen, 7, 80 - version_len - 2, version_str)

  #Update HELP_SCREEN
  help_screen = self.screens[App.HELP_SCREEN]

  help_screen.border(0)

  for index, pair in enumerate(HELP_MENU_ITEMS):
   key, value = pair

   self.addstr(help_screen, index + 1, 1, key + ' - ' + value)

 def draw_screens(self):
  for index, screen in enumerate(self.screens):
   if index == App.HELP_SCREEN:
    if not self.show_help:
     screen.erase()

   screen.noutrefresh()

  curses.doupdate()   

 #TODO Prevent Placeing STRs Outside of Terminal Bounds
 def addstr(self, screen, y, x, s, mode=curses.A_NORMAL):
  screen.addstr(y, x, s, mode)

 def get_down_count(self):
  count = 0

  for node in self.nodes:
   if node.state == Node.STATE_DOWN:
    count = count + 1

  return count

 def getstr(self):
  filter_str = ''

  curses.curs_set(1)
  curses.echo()

  root_screen = self.screens[App.ROOT_SCREEN]

  root_screen.move(15, 1)
  root_screen.nodelay(0)

  uppr_screen = self.screens[App.UPPR_SCREEN]

  self.addstr(uppr_screen, 15, 1, 'Filter:', curses.A_REVERSE)
  self.addstr(uppr_screen, 15, 8, '                  ', curses.A_REVERSE)
  uppr_screen.noutrefresh()

  root_screen.attron(curses.A_REVERSE)

  filter_str = root_screen.getstr(15, 9, 16)

  root_screen.attroff(curses.A_REVERSE)

  root_screen.nodelay(1)
  root_screen.move(0, 0)

  curses.noecho()
  curses.curs_set(0)

  return filter_str

 def load_hostfile(self):
  self.log('STARTUP: Loading ' + HOSTFILE + '...')

  #Load EVERY Host in HOSTFILE
  hostfile = open(HOSTFILE)

  try:
   for line in hostfile:
    line = line.strip()

    #Disregard Empty Lines, Comments, and IGNORE_HOSTS
    if not line:
     continue
    elif line.startswith('#'):
     continue
    elif any(h.lower() in line.lower() for h in IGNORE_HOSTS):
     continue

    line = line.split()

    #Prevent Incomplete Lines from Entering the List
    if len(line) >= 2:
     hostname = line[1]
     ip_addr  = line[0]

     self.nodes.append(Node(hostname, ip_addr))
  finally:
   hostfile.close()

  #Remove Duplicates (Order is Not Preserved)
  self.nodes = list(set(self.nodes))

  self.log('STARTUP: Loaded ' + str(len(self.nodes)) + ' hosts.')

 def log(self, message):
  log_time = datetime.now().strftime(LOG_TIME_FMT)

  self.logs.append(log_time + ' ' + message)

  if len(self.logs) > MAX_LOG_SIZE:
   del self.logs[:]

   self.log('CLEANUP: Max log size reached - logs cleared.')

 def ping(self, node):
  return os.system(PING_CMD.replace('%hostname', node.hostname))

 def ping_all(self):
  start_time = datetime.now()

  pool = ThreadPool(POOL_SIZE)

  results = pool.map(self.ping, self.nodes)

  pool.close()
  pool.join()

  end_time = datetime.now()
  total_time = end_time - start_time
  total_time_modified = total_time + timedelta(seconds=10)

  for result, node in zip(results, self.nodes):
   state = Node.STATE_UP if result == 0 else Node.STATE_DOWN

   is_dirty = node.set_state(state)

   if datetime.now() > self.start_time + total_time_modified:
    if is_dirty:
     self.dirty_nodes.append(node)

  self.last_ping_time = datetime.now()

 def sort_nodes(self, nodes):
  if self.current_sort == 0:
   nodes = sorted(nodes, key=lambda node : node.last_state_change, reverse=True)
  elif self.current_sort == 1:
   nodes = sorted(nodes, key=lambda node : node.hostname)

  return nodes

if __name__ == '__main__':
 curses.wrapper(App)
