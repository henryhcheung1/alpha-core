import math

from bitarray import bitarray
from database.dbc.DbcDatabaseManager import *
from database.realm.RealmDatabaseManager import RealmDatabaseManager
from database.world.WorldDatabaseManager import WorldDatabaseManager
from game.world.WorldSessionStateHandler import WorldSessionStateHandler
from game.world.managers.abstractions.Vector import Vector
from game.world.managers.maps.MapManager import MapManager
from game.world.managers.objects.gameobjects.GameObjectManager import GameObjectManager
from game.world.managers.objects.item.ItemManager import ItemManager
from game.world.managers.objects.loot.LootSelection import LootSelection
from game.world.managers.objects.spell.ExtendedSpellData import ShapeshiftInfo
from game.world.managers.objects.units.creature.CreatureManager import CreatureManager
from game.world.managers.objects.units.player.ChannelManager import ChannelManager
from game.world.managers.objects.units.player.EnchantmentManager import EnchantmentManager
from game.world.managers.objects.units.player.SkillManager import SkillManager
from game.world.managers.objects.units.player.TalentManager import TalentManager
from game.world.managers.objects.units.player.trade.TradeManager import TradeManager
from game.world.managers.objects.units.player.quest.QuestManager import QuestManager
from game.world.managers.objects.units.UnitManager import UnitManager
from game.world.managers.objects.units.player.FriendsManager import FriendsManager
from game.world.managers.objects.units.player.InventoryManager import InventoryManager
from game.world.managers.objects.units.player.ReputationManager import ReputationManager
from game.world.managers.objects.timers.MirrorTimersManager import MirrorTimersManager
from game.world.managers.objects.units.player.taxi.TaxiManager import TaxiManager
from game.world.opcode_handling.handlers.player.NameQueryHandler import NameQueryHandler
from network.packet.PacketWriter import *
from utils import Formulas
from utils.ByteUtils import ByteUtils
from utils.Logger import Logger
from utils.constants.DuelCodes import *
from utils.constants.ItemCodes import InventoryTypes
from utils.constants.MiscCodes import ChatFlags, LootTypes, LiquidTypes, MountResults, DismountResults
from utils.constants.MiscCodes import ObjectTypeFlags, ObjectTypeIds, PlayerFlags, WhoPartyStatus, HighGuid, \
    AttackTypes, MoveFlags
from utils.constants.SpellCodes import SpellSchools, SpellTargetMask
from utils.constants.UnitCodes import Classes, PowerTypes, Races, Genders, UnitFlags, Teams, SplineFlags, \
    RegenStatsFlags
from utils.constants.UpdateFields import *

MAX_ACTION_BUTTONS = 120
MAX_EXPLORED_AREAS = 488


class PlayerManager(UnitManager):
    def __init__(self,
                 player=None,
                 session=None,
                 num_inv_slots=0x89,  # Paperdoll + Bag slots + Bag space
                 player_bytes=0,  # skin, face, hair style, hair color
                 xp=0,
                 next_level_xp=0,
                 player_bytes_2=0,  # player flags, facial hair, bank slots, 0
                 block_percentage=0,
                 dodge_percentage=0,
                 parry_percentage=0,
                 combo_points=0,
                 combo_target=0,
                 chat_flags=0,
                 online=False,
                 current_selection=0,
                 deathbind=None,
                 **kwargs):
        super().__init__(**kwargs)

        self.session = session
        self.pending_teleport_destination = None
        self.pending_teleport_destination_map = -1
        self.pending_update_world_objects = False
        self.update_lock = False
        self.known_objects = dict()
        self.known_items = dict()

        self.player = player
        self.online = online
        self.num_inv_slots = num_inv_slots
        self.xp = xp
        self.next_level_xp = next_level_xp
        self.block_percentage = block_percentage
        self.dodge_percentage = dodge_percentage
        self.parry_percentage = parry_percentage
        self.combo_points = combo_points
        self.combo_target = combo_target

        self.current_selection = current_selection
        self.loot_selection: Optional[LootSelection] = None

        self.chat_flags = chat_flags
        self.group_status = WhoPartyStatus.WHO_PARTY_STATUS_NOT_IN_PARTY
        self.race_mask = 0
        self.class_mask = 0
        self.deathbind = deathbind
        self.team = Teams.TEAM_NONE  # Set at set_player_variables().
        self.trade_data = None
        self.last_swimming_check = 0
        self.spirit_release_timer = 0
        self.logout_timer = -1
        self.pending_taxi_destination = None
        self.explored_areas = bitarray(MAX_EXPLORED_AREAS, 'little')
        self.explored_areas.setall(0)
        self.liquid_information = None

        if self.player:
            self.race = player.race
            self.class_ = player.class_
            self.set_player_variables()
            self.guid = self.generate_object_guid(self.player.guid)
            self.inventory = InventoryManager(self)
            self.level = self.player.level
            self.player_bytes = self.get_player_bytes()
            self.player_bytes_2 = self.get_player_bytes_2()
            self.xp = player.xp
            self.talent_points = self.player.talentpoints
            self.skill_points = self.player.skillpoints
            self.map_ = self.player.map
            self.zone = self.player.zone
            self.location.x = self.player.position_x
            self.location.y = self.player.position_y
            self.location.z = self.player.position_z
            self.location.o = self.player.orientation
            self.health = self.player.health
            self.max_health = self.player.health
            self.max_power_1 = self.player.power1
            self.power_1 = self.player.power1
            self.max_power_2 = 1000
            self.power_2 = self.player.power2
            self.max_power_3 = 100
            self.power_3 = self.player.power3
            self.max_power_4 = 100
            self.power_4 = self.player.power4
            self.coinage = self.player.money
            self.regen_flags = RegenStatsFlags.REGEN_FLAG_HEALTH | RegenStatsFlags.REGEN_FLAG_POWER
            self.online = self.player.online

            # GM checks
            self.is_god = False
            self.is_gm = self.session.account_mgr.account.gmlevel > 0
            if self.is_gm:
                self.set_gm()

            # Cheat flags.
            self.beast_master = False

            # Update exploration data.
            if self.player.explored_areas and len(self.player.explored_areas) > 0:
                self.explored_areas = bitarray(self.player.explored_areas, 'little')

            self.next_level_xp = Formulas.PlayerFormulas.xp_to_level(self.level)
            self.is_alive = self.health > 0

            self.object_type_mask |= ObjectTypeFlags.TYPE_PLAYER
            self.update_packet_factory.init_values(self.guid, PlayerFields)

            self.unit_flags |= UnitFlags.UNIT_FLAG_PLAYER_CONTROLLED

            self.enchantment_manager = EnchantmentManager(self)
            self.talent_manager = TalentManager(self)
            self.skill_manager = SkillManager(self)
            self.quest_manager = QuestManager(self)
            self.friends_manager = FriendsManager(self)
            self.reputation_manager = ReputationManager(self)
            self.taxi_manager = TaxiManager(self)
            self.duel_manager = None
            self.guild_manager = None
            self.has_pending_group_invite = False
            self.group_manager = None
            self.mirror_timers_manager = MirrorTimersManager(self)

    def get_native_display_id(self, is_male, race_data=None):
        if not race_data:
            race_data = DbcDatabaseManager.chr_races_get_by_race(self.player.race)
        return race_data.MaleDisplayId if is_male else race_data.FemaleDisplayId

    def set_player_variables(self):
        race = DbcDatabaseManager.chr_races_get_by_race(self.race)

        self.faction = race.FactionID
        self.creature_type = race.CreatureType

        self.gender = self.player.gender
        is_male = self.gender == Genders.GENDER_MALE

        self.native_display_id = self.get_native_display_id(is_male, race)
        self.current_display_id = self.native_display_id

        # Initialize power type
        self.update_power_type()

        if self.race == Races.RACE_HUMAN:
            self.bounding_radius = 0.306 if is_male else 0.208
            self.combat_reach = 1.5
        elif self.race == Races.RACE_ORC:
            self.bounding_radius = 0.372 if is_male else 0.236
            self.combat_reach = 1.5
        elif self.race == Races.RACE_DWARF:
            self.bounding_radius = 0.347
            self.combat_reach = 1.5
        elif self.race == Races.RACE_NIGHT_ELF:
            self.bounding_radius = 0.389 if is_male else 0.306
            self.combat_reach = 1.5
        elif self.race == Races.RACE_UNDEAD:
            self.bounding_radius = 0.383
            self.combat_reach = 1.5
        elif self.race == Races.RACE_TAUREN:
            self.bounding_radius = 0.9747 if is_male else 0.8725
            self.combat_reach = 4.05 if is_male else 3.75
            self.native_scale = 1.35 if is_male else 1.25
        elif self.race == Races.RACE_GNOME:
            self.bounding_radius = 0.3519
            self.combat_reach = 1.725
            self.native_scale = 1.15
        elif self.race == Races.RACE_TROLL:
            self.bounding_radius = 0.306
            self.combat_reach = 1.5

        self.current_scale = self.native_scale
        self.race_mask = 1 << self.race - 1
        self.class_mask = 1 << self.class_ - 1

        self.team = PlayerManager.get_team_for_race(self.race)

    def set_gm(self, on=True):
        self.player.extra_flags |= PlayerFlags.PLAYER_FLAGS_GM
        self.chat_flags = ChatFlags.CHAT_TAG_GM

    def complete_login(self, first_login=False):
        self.online = True

        # Join default channels.
        ChannelManager.join_default_channels(self)

        # Init faction status.
        self.reputation_manager.send_initialize_factions()

        # If a flight needs to be resumed, make sure create packet uses last known waypoint location.
        taxi_resume_info = self.taxi_manager.taxi_resume_info
        if taxi_resume_info.is_valid():
            self.location = taxi_resume_info.start_location
            # Set player flags.
            self.set_taxi_flying_state(True, taxi_resume_info.mount_display_id)

        # Notify player with create related packets:
        self.enqueue_packet(NameQueryHandler.get_query_details(self.player))
        # Initial inventory create packets.
        self.enqueue_packets(self.inventory.get_inventory_update_packets(self))
        # Player create packet.
        self.enqueue_packet(self.generate_create_packet(requester=self))

        # Load & Apply enchantments.
        self.enchantment_manager.apply_enchantments(load=True)

        # Apply stat bonuses.
        self.stat_manager.apply_bonuses(replenish=first_login)

        # Place player in a world cell.
        MapManager.update_object(self)

        # Try to resume pending flight once player has been created and set on a world cell.
        if taxi_resume_info.is_valid() and not self.taxi_manager.resume_taxi_flight():
            self.set_taxi_flying_state(False)

        # Notify friends about player login.
        self.friends_manager.send_online_notification()

        # If guild, send guild Message of the Day.
        if self.guild_manager:
            self.guild_manager.send_motd(player_mgr=self)

        # Notify group members if needed.
        if self.group_manager:
            self.group_manager.send_update()

        self.spell_manager.send_login_effect()

    def logout(self):
        self.enqueue_packet(PacketWriter.get_packet(OpCode.SMSG_LOGOUT_COMPLETE))
        self.online = False
        self.logout_timer = -1
        self.mirror_timers_manager.stop_all()

        self.taxi_manager.update_flight_state()

        if self.duel_manager:
            self.duel_manager.force_duel_end(self)

        self.spell_manager.remove_all_casts()
        self.aura_manager.remove_all_auras()
        self.pet_manager.detach_active_pet()
        self.leave_combat(force=True)

        # Channels weren't saved on logout until Patch 0.5.5
        ChannelManager.leave_all_channels(self, logout=True)

        MapManager.remove_object(self)

        if self.group_manager:
            self.group_manager.send_update()

        self.friends_manager.send_offline_notification()
        self.session.save_character()

        # Destroy all known objects to self.
        for guid, known_object in list(self.known_objects.items()):
            self.destroy_near_object(guid)

        # Flush known items/objects cache.
        self.known_items.clear()
        self.known_objects.clear()

        WorldSessionStateHandler.pop_active_player(self)
        self.session.player_mgr = None
        self.session = None

    def get_tutorial_packet(self):
        return PacketWriter.get_packet(OpCode.SMSG_TUTORIAL_FLAGS, pack('<18I', 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                                                                        0, 0, 0, 0, 0))

    def get_action_buttons(self):
        data = b''
        player_buttons = RealmDatabaseManager.character_get_buttons(self.player.guid)
        for x in range(MAX_ACTION_BUTTONS):
            if player_buttons and x in player_buttons:
                data += pack('<i', player_buttons[x])
            else:
                data += pack('<i', 0)
        return PacketWriter.get_packet(OpCode.SMSG_ACTION_BUTTONS, data)

    def get_deathbind_packet(self):
        data = b''
        if self.deathbind:
            data = pack(
                '<3fI',
                self.deathbind.deathbind_position_x,
                self.deathbind.deathbind_position_y,
                self.deathbind.deathbind_position_z,
                self.deathbind.deathbind_map
            )
        return PacketWriter.get_packet(OpCode.SMSG_BINDPOINTUPDATE, data)

    # Retrieve update packets from world objects, this is called only if object has pending changes.
    # (update_mask bits set).
    def update_world_object_on_me(self, world_object, has_changes=False, has_inventory_changes=False):
        if world_object.guid in self.known_objects:
            is_player = world_object.get_type_id() == ObjectTypeIds.ID_PLAYER
            # Check for inventory updates.
            if is_player and has_inventory_changes:
                # This is a known player and has inventory changes.
                self.enqueue_packets(world_object.inventory.get_inventory_update_packets(self))
            # Update self with known world object partial update packet.
            if has_changes:
                self.enqueue_packet(world_object.generate_partial_packet(requester=self))
        elif world_object.guid == self.guid:  # Self (Player)
            # Update self inventory if needed.
            if has_inventory_changes:
                self.enqueue_packets(self.inventory.get_inventory_update_packets(self))
            # Send self a partial update if needed.
            if has_changes:
                self.enqueue_packet(self.generate_partial_packet(requester=self))

    # Notify self with create / destroy / partial movement packets of world objects in range.
    # Range = This player current active cell plus its adjacent cells.
    def update_known_world_objects(self):
        players, creatures, game_objects = MapManager.get_surrounding_objects(self, [ObjectTypeIds.ID_PLAYER,
                                                                                     ObjectTypeIds.ID_UNIT,
                                                                                     ObjectTypeIds.ID_GAMEOBJECT])

        # Which objects were found in self surroundings.
        active_objects = dict()

        # Surrounding players.
        for guid, player in players.items():
            if self.guid != guid:
                active_objects[guid] = player
                if guid not in self.known_objects or not self.known_objects[guid]:
                    # We don't know this player, notify self with its update packet.
                    self.enqueue_packet(NameQueryHandler.get_query_details(player.player))
                    # Retrieve their inventory updates.
                    self.enqueue_packets(player.inventory.get_inventory_update_packets(self))
                    # Create packet.
                    self.enqueue_packet(player.generate_create_packet(requester=self))
                    # Get partial movement packet if any.
                    if player.movement_manager.unit_is_moving():
                        packet = player.movement_manager.try_build_movement_packet(is_initial=False)
                        if packet:
                            self.enqueue_packet(packet)
                self.known_objects[guid] = player

        # Surrounding creatures.
        for guid, creature in creatures.items():
            active_objects[guid] = creature
            if guid not in self.known_objects or not self.known_objects[guid]:
                # We don't know this creature, notify self with its update packet.
                self.enqueue_packet(CreatureManager.query_details(creature_mgr=creature))
                if creature.is_spawned:
                    self.enqueue_packet(creature.generate_create_packet(requester=self))
                    # Get partial movement packet if any.
                    if creature.movement_manager.unit_is_moving():
                        packet = creature.movement_manager.try_build_movement_packet(is_initial=False)
                        if packet:
                            self.enqueue_packet(packet)
                    # We only consider 'known' if its spawned, the details query is still sent.
                    self.known_objects[guid] = creature
                    # Add ourselves to creature known players.
                    creature.known_players[self.guid] = self
                    # Notify this creature of our presence, e.g. player just logged in or a creature spawns near.
                    creature.notify_moved_in_line_of_sight(self)
            # Player knows the creature but is not spawned anymore, destroy it for self.
            elif guid in self.known_objects and not creature.is_spawned:
                active_objects.pop(guid)

        # Surrounding game objects.
        for guid, gobject in game_objects.items():
            active_objects[guid] = gobject
            if guid not in self.known_objects or not self.known_objects[guid]:
                # We don't know this game object, notify self with its update packet.
                self.enqueue_packet(GameObjectManager.query_details(gameobject_mgr=gobject))
                if gobject.is_spawned:
                    self.enqueue_packet(gobject.generate_create_packet(requester=self))
                    # We only consider 'known' if its spawned, the details query is still sent.
                    self.known_objects[guid] = gobject
                    # Add ourselves to gameobject known players.
                    gobject.known_players[self.guid] = self
            # Player knows the game object but is not spawned anymore, destroy it for self.
            elif guid in self.known_objects and not gobject.is_spawned:
                active_objects.pop(guid)

        # World objects which are known but no longer active to self should be destroyed.
        for guid, known_object in list(self.known_objects.items()):
            if guid not in active_objects:
                self.destroy_near_object(guid)

        # Cleanup.
        active_objects.clear()

    def destroy_near_object(self, guid):
        known_object = self.known_objects.get(guid)
        if known_object:
            self.known_objects.pop(guid, 'None')
            # Remove self from creature/go known players if needed.
            if known_object.get_type_id() != ObjectTypeIds.ID_PLAYER:
                if self.guid in known_object.known_players:
                    del known_object.known_players[self.guid]
            # Destroy world object from self.
            self.enqueue_packet(known_object.get_destroy_packet())
            return True
        return False

    def synchronize_db_player(self):
        if self.player:
            self.player.level = self.level
            self.player.xp = self.xp
            self.player.talentpoints = self.talent_points
            self.player.skillpoints = self.skill_points
            self.player.position_x = self.location.x
            self.player.position_y = self.location.y
            self.player.position_z = self.location.z
            self.player.map = self.map_
            self.player.orientation = self.location.o
            self.player.zone = self.zone
            self.player.explored_areas = self.explored_areas.to01()
            self.player.taximask = self.taxi_manager.available_taxi_nodes.to01()
            self.player.taxi_path = self.taxi_manager.taxi_resume_info.taxi_path_db_state
            self.player.health = self.health
            self.player.power1 = self.power_1
            self.player.power2 = self.power_2
            self.player.power3 = self.power_3
            self.player.power4 = self.power_4
            self.player.money = self.coinage
            self.player.online = self.online

    def teleport(self, map_, location, is_instant=False):
        if not DbcDatabaseManager.map_get_by_id(map_):
            return False

        if not MapManager.validate_teleport_destination(map_, location.x, location.y):
            return False

        # Make sure to end duel before starting the teleport process.
        if self.duel_manager:
            self.duel_manager.force_duel_end(self)

        # If unit is being moved by a spline, stop it.
        if self.movement_manager.unit_is_moving():
            self.movement_manager.reset()

        # Remove any ongoing cast.
        if self.spell_manager.is_casting():
            self.spell_manager.remove_all_casts()

        # TODO: Stop any movement, rotation?
        # New destination we will use when we receive an acknowledge message from client.
        self.pending_teleport_destination_map = map_
        self.pending_teleport_destination = Vector(location.x, location.y, location.z, location.o)

        if is_instant:
            self.trigger_teleport()

        return True

    def trigger_teleport(self):
        # From here on, the update is blocked until the player teleports to a new location.
        # If another teleport triggers from a client message, then it will proceed once this TP is done.
        self.update_lock = True

        # Same map.
        if self.map_ == self.pending_teleport_destination_map:
            data = pack(
                '<Q9fI',
                self.transport_id,
                self.transport.x,
                self.transport.y,
                self.transport.z,
                self.transport.o,
                self.pending_teleport_destination.x,
                self.pending_teleport_destination.y,
                self.pending_teleport_destination.z,
                self.pending_teleport_destination.o,
                self.pitch,
                MoveFlags.MOVEFLAG_NONE,
            )

            self.enqueue_packet(PacketWriter.get_packet(OpCode.MSG_MOVE_TELEPORT_ACK, data))

        # Different map, send loading screen.
        else:
            # Always remove the player from world before sending a Loading Screen, preventing unexpected packets
            # while the screen is still present.
            # Remove to others.
            MapManager.remove_object(self)
            self.enqueue_packet(PacketWriter.get_packet(OpCode.SMSG_TRANSFER_PENDING))

            data = pack(
                '<B4f',
                self.pending_teleport_destination_map,
                self.pending_teleport_destination.x,
                self.pending_teleport_destination.y,
                self.pending_teleport_destination.z,
                self.pending_teleport_destination.o
            )

            self.enqueue_packet(PacketWriter.get_packet(OpCode.SMSG_NEW_WORLD, data))

    def spawn_player_from_teleport(self):
        # Check if player changed maps before setting the new value.
        changed_map = self.map_ != self.pending_teleport_destination_map

        # Update new coordinates and map.
        if self.pending_teleport_destination_map != -1 and self.pending_teleport_destination:
            self.map_ = self.pending_teleport_destination_map
            self.location = Vector(self.pending_teleport_destination.x, self.pending_teleport_destination.y, self.pending_teleport_destination.z, self.pending_teleport_destination.o)

        # Player changed map. Send initial spells, action buttons and create packet.
        if changed_map:
            # Flush known items/objects cache.
            self.known_items.clear()
            self.known_objects.clear()
            # Send initial packets for spells, action buttons and player creation.
            self.enqueue_packet(self.spell_manager.get_initial_spells())
            self.enqueue_packet(self.get_action_buttons())
            # Inventory updates before spawning.
            self.enqueue_packets(self.inventory.get_inventory_update_packets(requester=self))
            # Create packet.
            self.enqueue_packet(self.generate_create_packet(requester=self))
            # Apply enchantments again.
            self.enchantment_manager.apply_enchantments()
            # Apply stat bonuses again.
            self.stat_manager.apply_bonuses()

        # Remove the player's active pet.
        self.pet_manager.detach_active_pet()

        # Remove taxi flying state, if any.
        if self.unit_flags & UnitFlags.UNIT_FLAG_TAXI_FLIGHT:
            self.set_taxi_flying_state(False)
            self.pending_taxi_destination = None

        # Unmount if needed.
        if self.unit_flags & UnitFlags.UNIT_MASK_MOUNTED:
            self.unmount()

        # Get us in a new cell.
        MapManager.update_object(self)

        # Notify movement data to surrounding players when teleporting within the same map (for example when using
        # Charge).
        # TODO: Can we somehow send MSG_MOVE_HEARTBEAT instead?
        if not changed_map:
            MapManager.send_surrounding(
                PacketWriter.get_packet(OpCode.SMSG_UPDATE_OBJECT, self.get_movement_update_packet()),
                self, False)

        self.pending_teleport_destination_map = -1
        self.pending_teleport_destination = None
        self.update_lock = False

        # Update managers.
        self.friends_manager.send_update_to_friends()
        if self.group_manager and self.group_manager.is_party_formed():
            self.group_manager.send_update()

    def set_root(self, active):
        super().set_root(active)
        if active:
            opcode = OpCode.SMSG_FORCE_MOVE_ROOT
        else:
            opcode = OpCode.SMSG_FORCE_MOVE_UNROOT
        self.enqueue_packet(PacketWriter.get_packet(opcode))

    # override
    def mount(self, mount_display_id):
        if super().mount(mount_display_id):
            # TODO, validate mount.
            data = pack('<QI', self.guid, MountResults.MOUNTRESULT_OK)
            packet = PacketWriter.get_packet(OpCode.SMSG_MOUNTRESULT, data)
            self.enqueue_packet(packet)
        else:
            data = pack('<QI', self.guid, MountResults.MOUNTRESULT_INVALID_MOUNTEE)
            packet = PacketWriter.get_packet(OpCode.SMSG_MOUNTRESULT, data)
            self.enqueue_packet(packet)

    # override
    def unmount(self):
        super().unmount()
        # TODO, validate dismount.
        data = pack('<QI', self.guid, DismountResults.DISMOUNT_RESULT_OK)
        packet = PacketWriter.get_packet(OpCode.SMSG_DISMOUNTRESULT, data)
        self.enqueue_packet(packet)

    # TODO Maybe merge all speed changes in one method
    # override
    def change_speed(self, speed=0):
        if super().change_speed(speed):
            data = pack('<f', self.running_speed)
            self.session.enqueue_packet(PacketWriter.get_packet(OpCode.SMSG_FORCE_SPEED_CHANGE, data))
            # TODO Move object update to UnitManager
            MapManager.send_surrounding(PacketWriter.get_packet(OpCode.SMSG_UPDATE_OBJECT,
                                                                self.get_movement_update_packet()), self)

    def change_swim_speed(self, swim_speed=0):
        if swim_speed <= 0:
            swim_speed = config.Unit.Defaults.swim_speed
        elif swim_speed >= 56:
            swim_speed = 56  # Max possible swim speed
        self.swim_speed = swim_speed
        data = pack('<f', self.swim_speed)
        self.enqueue_packet(PacketWriter.get_packet(OpCode.SMSG_FORCE_SWIM_SPEED_CHANGE, data))

        MapManager.send_surrounding(PacketWriter.get_packet(OpCode.SMSG_UPDATE_OBJECT,
                                                            self.get_movement_update_packet()), self)

    def change_walk_speed(self, walk_speed=0):
        if walk_speed <= 0:
            walk_speed = config.Unit.Defaults.walk_speed
        elif walk_speed >= 56:
            walk_speed = 56  # Max speed without glitches
        self.walk_speed = walk_speed
        data = pack('<f', self.walk_speed)
        self.enqueue_packet(PacketWriter.get_packet(OpCode.MSG_MOVE_SET_WALK_SPEED, data))

        MapManager.send_surrounding(PacketWriter.get_packet(OpCode.SMSG_UPDATE_OBJECT,
                                                            self.get_movement_update_packet()), self)

    def change_turn_speed(self, turn_speed=0):
        if turn_speed <= 0:
            turn_speed = config.Unit.Player.Defaults.turn_speed
        self.turn_rate = turn_speed
        data = pack('<f', self.turn_rate)
        self.enqueue_packet(PacketWriter.get_packet(OpCode.MSG_MOVE_SET_TURN_RATE, data))

        MapManager.send_surrounding(PacketWriter.get_packet(OpCode.SMSG_UPDATE_OBJECT,
                                                            self.get_movement_update_packet()), self)

    # override
    def update_power_type(self):
        if not self.shapeshift_form:
            if self.class_ == Classes.CLASS_WARRIOR:
                self.power_type = PowerTypes.TYPE_RAGE
            elif self.class_ == Classes.CLASS_HUNTER:
                self.power_type = PowerTypes.TYPE_FOCUS
            elif self.class_ == Classes.CLASS_ROGUE:
                self.power_type = PowerTypes.TYPE_ENERGY
            else:
                self.power_type = PowerTypes.TYPE_MANA
        else:
            self.power_type = ShapeshiftInfo.get_power_for_form(self.shapeshift_form)

        self.bytes_0 = self.get_bytes_0()
        self.set_uint32(UnitFields.UNIT_FIELD_BYTES_0, self.bytes_0)

    def loot_money(self):
        if self.loot_selection:
            enemy = MapManager.get_surrounding_unit_by_guid(self, self.loot_selection.object_guid)
            loot_manager = self.loot_selection.get_loot_manager(enemy)
            if enemy and loot_manager.has_money():
                # If party is formed, try to split money.
                if self.group_manager and self.group_manager.is_party_formed():
                    # Try to split money and finish on success.
                    if self.group_manager.reward_group_money(self, enemy):
                        return
                    else:  # Not able to split, notify the whole amount to the sole player.
                        data = pack('<I', loot_manager.current_money)
                        self.enqueue_packet(PacketWriter.get_packet(OpCode.SMSG_LOOT_MONEY_NOTIFY, data))

                # Not able to split money or no group, loot money to self only.
                self.mod_money(loot_manager.current_money)
                loot_manager.clear_money()
                packet = PacketWriter.get_packet(OpCode.SMSG_LOOT_CLEAR_MONEY)
                for looter in loot_manager.get_active_looters():
                    looter.enqueue_packet(packet)

    def loot_item(self, slot):
        if self.loot_selection:
            high_guid: HighGuid = self.extract_high_guid(self.loot_selection.object_guid)
            world_obj_target = None
            if high_guid == HighGuid.HIGHGUID_UNIT:
                world_obj_target = MapManager.get_surrounding_unit_by_guid(
                    self, self.loot_selection.object_guid, include_players=False)
            elif high_guid == HighGuid.HIGHGUID_GAMEOBJECT:
                world_obj_target = MapManager.get_surrounding_gameobject_by_guid(
                    self, self.loot_selection.object_guid)
            elif high_guid == HighGuid.HIGHGUID_ITEM:
                world_obj_target = self.inventory.get_item_by_guid(self.loot_selection.object_guid)

            loot_manager = self.loot_selection.get_loot_manager(world_obj_target)
            if world_obj_target and loot_manager and loot_manager.has_loot():
                loot = loot_manager.get_loot_in_slot(slot)
                if loot and loot.item:
                    if self.inventory.add_item(item_template=loot.item.item_template, count=loot.quantity, looted=True):
                        loot_manager.do_loot(slot, self)
                        data = pack('<B', slot)
                        packet = PacketWriter.get_packet(OpCode.SMSG_LOOT_REMOVED, data)
                        # Loot is multi-drop, notify only self about its removal.
                        if loot.is_multi_drop():
                            self.enqueue_packet(packet)
                        # Notify players with loot window open about its removal.
                        else:
                            for looter in loot_manager.get_active_looters():
                                looter.enqueue_packet(packet)

    def send_loot_release(self, loot_selection):
        self.unit_flags &= ~UnitFlags.UNIT_FLAG_LOOTING
        self.set_uint32(UnitFields.UNIT_FIELD_FLAGS, self.unit_flags)

        high_guid: HighGuid = self.extract_high_guid(self.loot_selection.object_guid)
        data = pack('<QB', loot_selection.object_guid, 1)  # Must be 1 otherwise client keeps the loot window open
        self.enqueue_packet(PacketWriter.get_packet(OpCode.SMSG_LOOT_RELEASE_RESPONSE, data))

        # Resolve loot target first.
        target_world_object = None
        if high_guid == HighGuid.HIGHGUID_UNIT:
            target_world_object = MapManager.get_surrounding_unit_by_guid(self, loot_selection.object_guid, include_players=False)
        elif high_guid == HighGuid.HIGHGUID_GAMEOBJECT:
            target_world_object = MapManager.get_surrounding_gameobject_by_guid(self, self.loot_selection.object_guid)
        elif high_guid == HighGuid.HIGHGUID_ITEM:
            target_world_object = self.inventory.get_item_by_guid(self.loot_selection.object_guid)
        else:
            Logger.warning(f'Unhandled loot release for type {HighGuid(high_guid).name}')

        if target_world_object:
            # Retrieve the loot manager for the corresponding world object.
            loot_manager = self.loot_selection.get_loot_manager(target_world_object)
            # Remove self from active looters.
            loot_manager.remove_active_looter(self)
            object_type = target_world_object.get_type_id()
            # UNITS.
            if object_type == ObjectTypeIds.ID_UNIT:
                enemy = target_world_object
                if loot_selection.loot_type != LootTypes.LOOT_TYPE_PICKLOCK:
                    # If this release comes from the loot owner and has no party, set killed_by to None to allow FFA.
                    if enemy.killed_by and enemy.killed_by == self and not enemy.killed_by.group_manager:
                        enemy.killed_by = None
                    # If in party, check if this player has rights to release the loot for FFA.
                    elif enemy.killed_by and enemy.killed_by.group_manager:
                        if self in enemy.killed_by.group_manager.get_allowed_looters(enemy):
                            if not loot_manager.has_loot():  # Flush looters for this enemy.
                                enemy.killed_by.group_manager.clear_looters_for_victim(enemy)
                            enemy.killed_by = None
                    # Empty loot, remove looting flags.
                    if not loot_manager.has_loot():
                        enemy.set_lootable(False)
            # GAMEOBJECTS.
            elif object_type == ObjectTypeIds.ID_GAMEOBJECT:
                game_object = target_world_object
                game_object.handle_loot_release(self)
            # ITEMS.
            elif object_type == ObjectTypeIds.ID_ITEM:
                item_mgr = target_world_object
                # Empty loot, remove item from player inventory bag.
                if not loot_manager.has_loot():
                    self.inventory.remove_item(item_mgr.item_instance.bag, item_mgr.current_slot)

            # Finally, clear the loot manager if it has no loot remaining.
            if not loot_manager.has_loot():
                loot_manager.clear()
        self.loot_selection = None

    def send_loot(self, loot_manager):
        loot_type = loot_manager.get_loot_type(self, loot_manager.world_object)
        self.loot_selection = LootSelection(loot_manager.world_object, loot_type)

        # Loot item data.
        item_data = b''
        # Items for query data.
        item_templates = []

        item_count = 0

        # Do not send loot if player has no permission.
        if loot_type != LootTypes.LOOT_TYPE_NOTALLOWED:
            slot = 0
            # Slot should match real current_loot indexes.
            for loot in loot_manager.current_loot:
                if loot:
                    # Skip conditions:
                    # - Is quest item and player does not have the involved quest.
                    # - Is quest multi-drop item and is no longer visible to this player.
                    if loot.is_quest_item() and \
                            not self.player_or_group_require_quest_item(loot.get_item_entry(), only_self=True) or \
                            not loot.is_visible_to_player(self):
                        slot += 1
                        continue

                    item_templates.append(loot.item.item_template)
                    item_count += 1

                    item_data += pack(
                        '<B3I',
                        slot,
                        loot.item.item_template.entry,
                        loot.quantity,
                        loot.item.item_template.display_id,
                    )
                slot += 1

            # At this point, this player has access to the loot window, add him to the active looters.
            loot_manager.add_active_looter(self)

        # Set the header, now that we know how many actual items were sent.
        data = pack(
            '<QBIB',
            loot_manager.world_object.guid,
            loot_type,
            loot_manager.current_money,
            item_count
        )

        # Append item data and send all the packed item detail queries for current loot, if any.
        if item_count:
            data += item_data
            self.enqueue_packets(ItemManager.get_item_query_packets(item_templates))

        packet = PacketWriter.get_packet(OpCode.SMSG_LOOT_RESPONSE, data)
        self.enqueue_packet(packet)

        return loot_type != LootTypes.LOOT_TYPE_NOTALLOWED

    def reward_reputation_on_kill(self, creature, rate=1.0):
        reputation_on_kill_entry = WorldDatabaseManager.CreatureOnKillReputationHolder.\
            creature_on_kill_reputation_get_by_entry(creature.entry)
        if not reputation_on_kill_entry:
            return
        self.reputation_manager.reward_reputation_on_kill(creature, rate)

    def give_xp(self, amounts, victim=None, notify=True):
        if self.level >= config.Unit.Player.Defaults.max_level or not self.is_alive:
            return 0

        """
        0.5.3 supports multiple amounts of XP and then combines them all

        uint64_t victim,
        uint32_t count

        loop (for each count):
            uint64_t guid,
            int32_t xp
        """

        amount_bytes = b''
        total_amount = 0
        for amount in amounts:
            # Adjust XP gaining rates using config
            amount = int(amount * config.Server.Settings.xp_rate)

            total_amount += amount
            amount_bytes += pack('<QI', self.guid, amount)

        if notify:
            data = pack('<QI',
                        victim.guid if victim else self.guid,
                        len(amounts)
                        )
            data += amount_bytes
            self.enqueue_packet(PacketWriter.get_packet(OpCode.SMSG_LOG_XPGAIN, data))

        # Reward kill experience to pet.
        if victim:
            self.pet_manager.add_active_pet_experience(total_amount)

        if self.xp + total_amount >= self.next_level_xp:  # Level up!
            xp_to_level = self.next_level_xp - self.xp
            level_amount = 0
            remaining_amount = total_amount
            # Do the actual XP conversion into level(s).
            while remaining_amount >= xp_to_level:
                level_amount += 1
                remaining_amount -= xp_to_level
                xp_to_level = Formulas.PlayerFormulas.xp_to_level(self.level + level_amount)

            self.xp = remaining_amount  # Set the remaining amount XP as current.
            self.set_uint32(PlayerFields.PLAYER_XP, self.xp)
            self.mod_level(self.level + level_amount)
        else:
            self.xp = self.xp + total_amount
            self.set_uint32(PlayerFields.PLAYER_XP, self.xp)

        return total_amount

    def mod_level(self, level):
        if level != self.level:
            max_level = 255 if self.is_gm else config.Unit.Player.Defaults.max_level
            if 0 < level <= max_level:
                # Check if the new level is higher than the current one or not.
                is_leveling_up = level > self.level
                # Store the difference between the starting level and the target level.
                level_count = abs(level - self.level)

                # Calculate total talent and skill points for each level starting from the current character level.
                talent_points = 0
                skill_points = 0
                for i in range(level_count):
                    if is_leveling_up:
                        level_for_calculation = self.level + (i + 1)
                    else:
                        level_for_calculation = self.level - i
                    talent_points += Formulas.PlayerFormulas.talent_points_gain_per_level(level_for_calculation)
                    skill_points += Formulas.PlayerFormulas.skill_points_gain_per_level(level_for_calculation)

                if is_leveling_up:
                    # Add Talent and Skill points.
                    self.add_talent_points(talent_points)
                    self.add_skill_points(skill_points)
                else:
                    # Remove Talent and Skill points.
                    self.remove_talent_points(talent_points)
                    self.remove_skill_points(skill_points)

                self.level = level
                self.set_uint32(UnitFields.UNIT_FIELD_LEVEL, self.level)
                self.player.leveltime = 0

                self.stat_manager.init_stats()
                hp_diff, mana_diff = self.stat_manager.apply_bonuses()
                self.set_health(self.max_health)
                self.set_mana(self.max_power_1)

                self.skill_manager.update_skills_max_value()
                self.skill_manager.build_update()

                if is_leveling_up:
                    data = pack(
                        '<3I',
                        level,
                        hp_diff,
                        mana_diff if self.power_type == PowerTypes.TYPE_MANA else 0
                    )

                    self.enqueue_packet(PacketWriter.get_packet(OpCode.SMSG_LEVELUP_INFO, data))

                self.next_level_xp = Formulas.PlayerFormulas.xp_to_level(self.level)
                self.set_uint32(PlayerFields.PLAYER_NEXT_LEVEL_XP, self.next_level_xp)
                self.quest_manager.update_surrounding_quest_status()
                self.friends_manager.send_update_to_friends()

    def player_or_group_require_quest_item(self, item_entry, only_self=False):
        if not self.group_manager or only_self:
            return self.quest_manager.item_needed_by_quests(item_entry)
        else:
            for member in self.group_manager.members.values():
                player_mgr = WorldSessionStateHandler.find_player_by_guid(member.guid)
                if player_mgr and player_mgr.quest_manager.item_needed_by_quests(item_entry):
                    return True
        return False

    def add_bank_slot(self, slot_cost):
        self.player.bankslots += 1
        self.player_bytes_2 = self.get_player_bytes_2()
        self.set_uint32(PlayerFields.PLAYER_BYTES_2, self.player_bytes_2)
        self.mod_money(-slot_cost)

    def mod_money(self, amount):
        if self.coinage + amount < 0:
            amount = -self.coinage

        # Gold hard cap: 214748 gold, 36 silver and 47 copper
        if self.coinage + amount > 2147483647:
            self.coinage = 2147483647
        else:
            self.coinage += amount

        self.set_uint32(UnitFields.UNIT_FIELD_COINAGE, self.coinage)

    def on_zone_change(self, new_zone):
        # Update player zone.
        self.zone = new_zone
        # Update friends and group.
        self.friends_manager.send_update_to_friends()
        if self.group_manager:
            self.group_manager.send_update()

        # Checks below this condition can only happen if map loading is enabled.
        if not config.Server.Settings.use_map_tiles:
            return

        # Exploration handling (only if player is not flying).
        if not self.movement_spline or self.movement_spline.flags != SplineFlags.SPLINEFLAG_FLYING:
            area_information = MapManager.get_area_information(self.map_, self.location.x, self.location.y)
            if area_information:
                # Check if we need to set this zone as explored.
                if area_information.explore_bit >= 0 and not self.has_area_explored(area_information.explore_bit):
                    self.set_area_explored(area_information)

    def has_area_explored(self, area_explore_bit):
        return self.explored_areas[area_explore_bit]

    # TODO, Trigger quest explore requirement checks.
    def set_area_explored(self, area_information):
        self.explored_areas[area_information.explore_bit] = True
        if area_information.level > 0:
            if self.level < config.Unit.Player.Defaults.max_level:
                # The following calculations are taken from VMaNGOS core.
                xp_rate = int(config.Server.Settings.xp_rate)
                diff = self.level - area_information.level
                if diff < -5:
                    xp_gain = WorldDatabaseManager.exploration_base_xp_get_by_level(self.level + 5) * xp_rate
                elif diff > 5:
                    exploration_percent = (100 - ((diff - 5) * 5))
                    if exploration_percent > 100:
                        exploration_percent = 100
                    elif exploration_percent < 0:
                        exploration_percent = 0
                    xp_gain = WorldDatabaseManager.exploration_base_xp_get_by_level(area_information.level) * exploration_percent / 100 * xp_rate
                else:
                    xp_gain = WorldDatabaseManager.exploration_base_xp_get_by_level(area_information.level) * xp_rate
                self.give_xp([xp_gain], notify=False)
            else:
                xp_gain = 0

            # Notify client new discovered zone + xp gain.
            data = pack('<2I', area_information.zone_id, int(xp_gain * config.Server.Settings.xp_rate))
            packet = PacketWriter.get_packet(OpCode.SMSG_EXPLORATION_EXPERIENCE, data)
            self.enqueue_packet(packet)

    def update_swimming_state(self, state):
        if state:
            self.liquid_information = MapManager.get_liquid_information(self.map_, self.location.x, self.location.y, self.location.z)
            if not self.liquid_information:
                Logger.warning(f'Unable to retrieve liquid information.')
        else:
            self.liquid_information = None

    def is_swimming(self):
        return self.movement_flags & MoveFlags.MOVEFLAG_SWIMMING and self.is_alive

    # override
    def is_on_water(self):
        self.liquid_information = MapManager.get_liquid_information(self.map_, self.location.x, self.location.y, self.location.z)
        return self.liquid_information and self.liquid_information.height > self.location.z

    # override
    def is_under_water(self):
        if self.liquid_information is None or not self.is_swimming():
            return False
        return self.location.z + (self.current_scale * 2) < self.liquid_information.height

    # override
    def is_in_deep_water(self):
        if self.liquid_information is None or not self.is_swimming():
            return False
        return self.liquid_information.liquid_type == LiquidTypes.DEEP

    def update_liquid_information(self):
        # Retrieve the latest liquid information, only if player is swimming.
        if self.is_swimming():
            self.liquid_information = MapManager.get_liquid_information(self.map_, self.location.x, self.location.y, self.location.z)

    # override
    def initialize_field_values(self):
        # Initial field values, after this, fields must be modified by setters or directly writing values to them.
        if not self.initialized:
            self.bytes_0 = self.get_bytes_0()
            self.bytes_1 = self.get_bytes_1()
            self.bytes_2 = self.get_bytes_2()
            self.player_bytes_2 = self.get_player_bytes_2()

            # Object fields.
            self.set_uint64(ObjectFields.OBJECT_FIELD_GUID, self.player.guid)
            self.set_uint32(ObjectFields.OBJECT_FIELD_TYPE, self.object_type_mask)
            self.set_uint32(ObjectFields.OBJECT_FIELD_ENTRY, self.entry)
            self.set_float(ObjectFields.OBJECT_FIELD_SCALE_X, self.current_scale)

            # Unit fields.
            self.set_uint32(UnitFields.UNIT_CHANNEL_SPELL, self.channel_spell)
            self.set_uint64(UnitFields.UNIT_FIELD_CHANNEL_OBJECT, self.channel_object)
            self.set_uint32(UnitFields.UNIT_FIELD_HEALTH, self.health)
            self.set_uint32(UnitFields.UNIT_FIELD_POWER1, self.power_1)
            self.set_uint32(UnitFields.UNIT_FIELD_POWER2, self.power_2)
            self.set_uint32(UnitFields.UNIT_FIELD_POWER3, self.power_3)
            self.set_uint32(UnitFields.UNIT_FIELD_POWER4, self.power_4)
            self.set_uint32(UnitFields.UNIT_FIELD_MAXHEALTH, self.max_health)
            self.set_uint32(UnitFields.UNIT_FIELD_MAXPOWER1, self.max_power_1)
            self.set_uint32(UnitFields.UNIT_FIELD_MAXPOWER2, self.max_power_2)
            self.set_uint32(UnitFields.UNIT_FIELD_MAXPOWER3, self.max_power_3)
            self.set_uint32(UnitFields.UNIT_FIELD_MAXPOWER4, self.max_power_4)
            self.set_uint32(UnitFields.UNIT_FIELD_LEVEL, self.level)
            self.set_uint32(UnitFields.UNIT_FIELD_FACTIONTEMPLATE, self.faction)
            self.set_uint32(UnitFields.UNIT_FIELD_BYTES_0, self.bytes_0)
            self.set_uint32(UnitFields.UNIT_FIELD_STAT0, self.str)
            self.set_uint32(UnitFields.UNIT_FIELD_STAT1, self.agi)
            self.set_uint32(UnitFields.UNIT_FIELD_STAT2, self.sta)
            self.set_uint32(UnitFields.UNIT_FIELD_STAT3, self.int)
            self.set_uint32(UnitFields.UNIT_FIELD_STAT4, self.spi)
            self.set_uint32(UnitFields.UNIT_FIELD_BASESTAT0, self.base_str)
            self.set_uint32(UnitFields.UNIT_FIELD_BASESTAT1, self.base_agi)
            self.set_uint32(UnitFields.UNIT_FIELD_BASESTAT2, self.base_sta)
            self.set_uint32(UnitFields.UNIT_FIELD_BASESTAT3, self.base_int)
            self.set_uint32(UnitFields.UNIT_FIELD_BASESTAT4, self.base_spi)
            self.set_uint32(UnitFields.UNIT_FIELD_FLAGS, self.unit_flags)
            self.set_uint32(UnitFields.UNIT_FIELD_COINAGE, self.coinage)
            self.set_uint32(UnitFields.UNIT_FIELD_BASEATTACKTIME, self.base_attack_time)
            self.set_uint32(UnitFields.UNIT_FIELD_BASEATTACKTIME + 1, self.offhand_attack_time)
            self.set_int64(UnitFields.UNIT_FIELD_RESISTANCES, self.resistance_0)
            self.set_int32(UnitFields.UNIT_FIELD_RESISTANCES + 1, self.resistance_1)
            self.set_int32(UnitFields.UNIT_FIELD_RESISTANCES + 2, self.resistance_2)
            self.set_int32(UnitFields.UNIT_FIELD_RESISTANCES + 3, self.resistance_3)
            self.set_int32(UnitFields.UNIT_FIELD_RESISTANCES + 4, self.resistance_4)
            self.set_int32(UnitFields.UNIT_FIELD_RESISTANCES + 5, self.resistance_5)
            self.set_float(UnitFields.UNIT_FIELD_BOUNDINGRADIUS, self.bounding_radius)
            self.set_float(UnitFields.UNIT_FIELD_COMBATREACH, self.combat_reach)
            self.set_float(UnitFields.UNIT_FIELD_WEAPONREACH, self.weapon_reach)
            self.set_uint32(UnitFields.UNIT_FIELD_DISPLAYID, self.current_display_id)
            self.set_uint32(UnitFields.UNIT_FIELD_MOUNTDISPLAYID, self.mount_display_id)
            self.set_int32(UnitFields.UNIT_FIELD_RESISTANCEBUFFMODSPOSITIVE, self.resistance_buff_mods_positive_0)
            self.set_int32(UnitFields.UNIT_FIELD_RESISTANCEBUFFMODSPOSITIVE + 1, self.resistance_buff_mods_positive_1)
            self.set_int32(UnitFields.UNIT_FIELD_RESISTANCEBUFFMODSPOSITIVE + 2, self.resistance_buff_mods_positive_2)
            self.set_int32(UnitFields.UNIT_FIELD_RESISTANCEBUFFMODSPOSITIVE + 3, self.resistance_buff_mods_positive_3)
            self.set_int32(UnitFields.UNIT_FIELD_RESISTANCEBUFFMODSPOSITIVE + 4, self.resistance_buff_mods_positive_4)
            self.set_int32(UnitFields.UNIT_FIELD_RESISTANCEBUFFMODSPOSITIVE + 5, self.resistance_buff_mods_positive_5)
            self.set_int32(UnitFields.UNIT_FIELD_RESISTANCEBUFFMODSNEGATIVE, self.resistance_buff_mods_negative_0)
            self.set_int32(UnitFields.UNIT_FIELD_RESISTANCEBUFFMODSNEGATIVE + 1, self.resistance_buff_mods_negative_1)
            self.set_int32(UnitFields.UNIT_FIELD_RESISTANCEBUFFMODSNEGATIVE + 2, self.resistance_buff_mods_negative_2)
            self.set_int32(UnitFields.UNIT_FIELD_RESISTANCEBUFFMODSNEGATIVE + 3, self.resistance_buff_mods_negative_3)
            self.set_int32(UnitFields.UNIT_FIELD_RESISTANCEBUFFMODSNEGATIVE + 4, self.resistance_buff_mods_negative_4)
            self.set_int32(UnitFields.UNIT_FIELD_RESISTANCEBUFFMODSNEGATIVE + 5, self.resistance_buff_mods_negative_5)
            self.set_uint32(UnitFields.UNIT_FIELD_BYTES_1, self.bytes_1)
            self.set_float(UnitFields.UNIT_MOD_CAST_SPEED, self.mod_cast_speed)
            self.set_uint32(UnitFields.UNIT_DYNAMIC_FLAGS, self.dynamic_flags)
            self.set_uint32(UnitFields.UNIT_FIELD_DAMAGE, self.damage)
            self.set_uint32(UnitFields.UNIT_FIELD_BYTES_2, self.bytes_2)

            # Player fields.
            self.set_uint32(PlayerFields.PLAYER_FIELD_NUM_INV_SLOTS, self.num_inv_slots)
            self.set_uint32(PlayerFields.PLAYER_BYTES, self.player_bytes)
            self.set_uint32(PlayerFields.PLAYER_XP, self.xp)
            self.set_uint32(PlayerFields.PLAYER_NEXT_LEVEL_XP, self.next_level_xp)
            self.set_uint32(PlayerFields.PLAYER_BYTES_2, self.player_bytes_2)
            self.set_uint32(PlayerFields.PLAYER_CHARACTER_POINTS1, self.talent_points)
            self.set_uint32(PlayerFields.PLAYER_CHARACTER_POINTS2, self.skill_points)
            self.set_float(PlayerFields.PLAYER_BLOCK_PERCENTAGE, self.block_percentage)
            self.set_float(PlayerFields.PLAYER_DODGE_PERCENTAGE, self.dodge_percentage)
            self.set_float(PlayerFields.PLAYER_PARRY_PERCENTAGE, self.parry_percentage)
            self.set_uint32(PlayerFields.PLAYER_BASE_MANA, self.base_mana)

            # Skills.
            self.skill_manager.build_update()

            # Guild.
            if self.guild_manager:
                self.guild_manager.build_update(self)

            # Duel.
            if self.duel_manager:
                self.duel_manager.build_update(self)

            # Inventory.
            self.inventory.build_update()

            # Auras.
            self.aura_manager.build_update()

            # Quests.
            self.quest_manager.build_update()

            self.initialized = True

    def set_current_selection(self, guid):
        self.current_selection = guid
        self.set_uint64(PlayerFields.PLAYER_SELECTION, guid)

    def set_weapon_reach(self, reach):
        self.weapon_reach = reach
        self.set_float(UnitFields.UNIT_FIELD_WEAPONREACH, reach)

    def set_base_str(self, str_):
        self.base_str = str_
        self.set_uint32(UnitFields.UNIT_FIELD_BASESTAT0, str_)

    def set_base_agi(self, agi):
        self.base_agi = agi
        self.set_uint32(UnitFields.UNIT_FIELD_BASESTAT1, agi)

    def set_base_sta(self, sta):
        self.base_sta = sta
        self.set_uint32(UnitFields.UNIT_FIELD_BASESTAT2, sta)

    def set_base_int(self, int_):
        self.base_int = int_
        self.set_uint32(UnitFields.UNIT_FIELD_BASESTAT3, int_)

    def set_base_spi(self, spi):
        self.base_spi = spi
        self.set_uint32(UnitFields.UNIT_FIELD_BASESTAT4, spi)

    def set_str(self, str_):
        self.str = str_
        self.set_int32(UnitFields.UNIT_FIELD_STAT0, str_)

    def set_agi(self, agi):
        self.agi = agi
        self.set_int32(UnitFields.UNIT_FIELD_STAT1, agi)

    def set_sta(self, sta):
        self.sta = sta
        self.set_int32(UnitFields.UNIT_FIELD_STAT2, sta)

    def set_int(self, int_):
        self.int = int_
        self.set_int32(UnitFields.UNIT_FIELD_STAT3, int_)

    def set_spi(self, spi):
        self.spi = spi
        self.set_int32(UnitFields.UNIT_FIELD_STAT4, spi)

    def set_block_chance(self, block):
        self.block_percentage = block
        self.set_float(PlayerFields.PLAYER_BLOCK_PERCENTAGE, block)

    def set_parry_chance(self, parry):
        self.parry_percentage = parry
        self.set_float(PlayerFields.PLAYER_PARRY_PERCENTAGE, parry)

    def set_dodge_chance(self, dodge):
        self.dodge_percentage = dodge
        self.set_float(PlayerFields.PLAYER_DODGE_PERCENTAGE, dodge)

    def add_talent_points(self, talent_points):
        self.talent_points += talent_points
        self.set_uint32(PlayerFields.PLAYER_CHARACTER_POINTS1, self.talent_points)

    def add_skill_points(self, skill_points):
        self.skill_points += skill_points
        self.set_uint32(PlayerFields.PLAYER_CHARACTER_POINTS2, self.skill_points)

    def remove_talent_points(self, talent_points):
        self.talent_points = max(0, self.talent_points - talent_points)
        self.set_uint32(PlayerFields.PLAYER_CHARACTER_POINTS1, self.talent_points)

    def remove_skill_points(self, skill_points):
        self.skill_points = max(0, self.skill_points - skill_points)
        self.set_uint32(PlayerFields.PLAYER_CHARACTER_POINTS2, self.skill_points)

    # override
    def calculate_spell_damage(self, base_damage, spell_school: SpellSchools, target, spell_attack_type: AttackTypes = -1):
        subclass = 0
        if spell_attack_type != -1:
            equipped_weapon = self.get_current_weapon_for_attack_type(spell_attack_type)
            if equipped_weapon:
                subclass = equipped_weapon.item_template.subclass

        damage = self.stat_manager.apply_bonuses_for_damage(base_damage, spell_school, target, subclass)
        return max(0, int(damage))

    # override
    def handle_combat_skill_gain(self, damage_info):
        if damage_info.attacker == self:
            self.skill_manager.handle_weapon_skill_gain_chance(damage_info.attack_type)
        else:
            self.skill_manager.handle_defense_skill_gain_chance(damage_info)

    # override
    def handle_melee_attack_procs(self, damage_info):
        super().handle_melee_attack_procs(damage_info)
        self.enchantment_manager.handle_melee_attack_procs(damage_info)

    def _send_attack_swing_error(self, victim, opcode):
        data = pack('<2Q', self.guid, victim.guid if victim else 0)
        self.enqueue_packet(PacketWriter.get_packet(opcode, data))

    # override
    def send_attack_swing_not_in_range(self, victim):
        self._send_attack_swing_error(victim, OpCode.SMSG_ATTACKSWING_NOTINRANGE)

    # override
    def send_attack_swing_facing_wrong_way(self, victim):
        self._send_attack_swing_error(victim, OpCode.SMSG_ATTACKSWING_BADFACING)

    # override
    def send_attack_swing_cant_attack(self, victim):
        self._send_attack_swing_error(victim, OpCode.SMSG_ATTACKSWING_CANT_ATTACK)

    # override
    def send_attack_swing_dead_target(self, victim):
        self._send_attack_swing_error(victim, OpCode.SMSG_ATTACKSWING_DEADTARGET)

    # override
    def send_attack_swing_not_standing(self, victim):
        self._send_attack_swing_error(victim, OpCode.SMSG_ATTACKSWING_NOTSTANDING)

    # override
    def has_offhand_weapon(self):
        return self.inventory.has_offhand_weapon()

    # override
    def has_ranged_weapon(self):
        return self.inventory.has_ranged_weapon()

    # override
    def can_block(self, attacker_location=None):
        if not super().can_block(attacker_location):
            return False

        if attacker_location and not self.location.has_in_arc(attacker_location, math.pi):
            return False  # players can't block from behind.

        return self.inventory.has_offhand() and \
            self.inventory.get_offhand().item_template.inventory_type == InventoryTypes.SHIELD

    # override
    def can_parry(self, attacker_location=None):
        if not super().can_parry(attacker_location):
            return False

        if attacker_location and not self.location.has_in_arc(attacker_location, math.pi):
            return False  # players can't parry from behind.

        return

    # override
    def can_dodge(self, attacker_location=None):
        if not super().can_dodge(attacker_location):
            return False

        if attacker_location and not self.location.has_in_arc(attacker_location, math.pi):
            return False  # players can't dodge from behind.

        return True  # TODO Stunned check

    # override
    def set_weapon_mode(self, weapon_mode):
        super().set_weapon_mode(weapon_mode)
        self.bytes_1 = self.get_bytes_1()
        self.set_uint32(UnitFields.UNIT_FIELD_BYTES_1, self.bytes_1)

    # override
    def set_stand_state(self, stand_state):
        super().set_stand_state(stand_state)
        self.bytes_1 = self.get_bytes_1()
        self.set_uint32(UnitFields.UNIT_FIELD_BYTES_1, self.bytes_1)

    # override
    def set_shapeshift_form(self, shapeshift_form):
        super().set_shapeshift_form(shapeshift_form)
        self.bytes_1 = self.get_bytes_1()
        self.set_uint32(UnitFields.UNIT_FIELD_BYTES_1, self.bytes_1)

    # override
    def add_combo_points_on_target(self, target, combo_points, hide=False):
        if combo_points <= 0 or not target.is_alive:  # Killing a unit with a combo generator can generate a combo point after death
            return

        if target.guid != self.combo_target:
            self.combo_points = min(combo_points, 5)
            self.combo_target = target.guid
        else:
            self.combo_points = min(combo_points + self.combo_points, 5)

        self.bytes_2 = self.get_bytes_2()
        self.set_uint32(UnitFields.UNIT_FIELD_BYTES_2, self.bytes_2)

        if not hide:
            self.combo_target = target.guid
            self.set_uint64(UnitFields.UNIT_FIELD_COMBO_TARGET, self.combo_target)

    # override
    def remove_combo_points(self):
        self.combo_points = 0
        self.bytes_2 = self.get_bytes_2()
        self.set_uint32(UnitFields.UNIT_FIELD_BYTES_2, self.bytes_2)

        self.combo_target = 0
        self.set_uint64(UnitFields.UNIT_FIELD_COMBO_TARGET, self.combo_target)

    # override
    def receive_damage(self, amount, source=None, is_periodic=False, casting_spell=None):
        if self.is_god:
            return

        super().receive_damage(amount, source, is_periodic=False)

    # override
    def receive_healing(self, amount, source=None):
        super().receive_healing(amount, source)

        data = pack('<IQ', amount, source.guid)
        self.enqueue_packet(PacketWriter.get_packet(OpCode.SMSG_HEALSPELL_ON_PLAYER, data))

    def enqueue_packets(self, packets):
        if self.session:
            self.session.enqueue_packets(packets)
        else:
            Logger.warning('Tried to send packet to null session.')

    def enqueue_packet(self, data):
        if self.session:
            self.session.enqueue_packet(data)
        else:
            Logger.warning('Tried to send packet to null session.')

    def check_swimming_state(self, elapsed):
        if not self.is_alive:
            return

        self.last_swimming_check += elapsed
        if self.last_swimming_check >= 1:
            self.last_swimming_check = 0
            if self.is_swimming() and not self.liquid_information:
                self.update_swimming_state(True)
            elif not self.is_swimming() and self.liquid_information:
                self.update_swimming_state(False)

    # override
    def update(self, now):
        if now > self.last_tick > 0 and self.online:
            elapsed = now - self.last_tick

            # Update played time.
            self.player.totaltime += elapsed
            self.player.leveltime += elapsed

            # Update surrounding world objects visibility if needed.
            if self.pending_update_world_objects:
                self.pending_update_world_objects = False
                self.update_known_world_objects()

            # Regeneration.
            self.regenerate(elapsed)
            # Attack update.
            self.attack_update(elapsed)
            # Check swimming state.
            self.check_swimming_state(elapsed)

            # SpellManager tick.
            self.spell_manager.update(now)
            # AuraManager tick.
            self.aura_manager.update(now)

            # Waypoints (mostly flying paths) update.
            self.movement_manager.update_pending_waypoints(elapsed)
            # Movement checks.
            if self.has_moved:
                self._on_relocation()
                self.set_has_moved(False)

            # Duel tick.
            if self.duel_manager:
                self.duel_manager.update(self, elapsed)

            # Enchantment manager.
            self.enchantment_manager.update(elapsed)

            # Release spirit timer.
            if not self.is_alive:
                if self.spirit_release_timer < 300:  # 5 min.
                    self.spirit_release_timer += elapsed
                else:
                    self.repop()

            # Update timers (Breath, Fatigue, Feign Death).
            if self.is_alive:
                self.mirror_timers_manager.update(elapsed)

            # Logout timer.
            if self.logout_timer > 0:
                self.logout_timer -= elapsed
                if self.logout_timer < 0:
                    self.logout()
                    return

            has_changes = self.has_pending_updates()
            # Avoid inventory/item update if there is an ongoing inventory operation.
            has_inventory_changes = not self.inventory.update_locked and self.inventory.has_pending_updates()
            # Check if player has pending fields or inventory updates.
            if self.online and has_changes or has_inventory_changes:
                MapManager.update_object(self, has_changes=has_changes, has_inventory_changes=has_inventory_changes)
                if has_changes:
                    self.reset_fields_older_than(now)
                if has_inventory_changes:
                    self.inventory.reset_fields_older_than(now)
            # Not dirty, has a pending teleport and a teleport is not ongoing.
            elif not has_changes and not has_inventory_changes and self.pending_teleport_destination \
                    and not self.update_lock:
                self.trigger_teleport()
            # Do normal update.
            else:
                MapManager.update_object(self)
                self.synchronize_db_player()

        self.last_tick = now

    # override
    def attack_update(self, elapsed):
        # If we have a combat target, no attackers and target is no longer alive or is evading, leave combat.
        if self.combat_target and (not self.combat_target.is_alive or self.combat_target.is_evading):
            if len(self.attackers) == 0:
                self.leave_combat()
                return

        super().attack_update(elapsed)

    def teleport_deathbind(self):
        self.teleport(self.deathbind.deathbind_map, Vector(self.deathbind.deathbind_position_x,
                                                           self.deathbind.deathbind_position_y,
                                                           self.deathbind.deathbind_position_z))

    def get_deathbind_coordinates(self):
        return (self.deathbind.deathbind_map, Vector(self.deathbind.deathbind_position_x,
                                                     self.deathbind.deathbind_position_y,
                                                     self.deathbind.deathbind_position_z))

    # override
    def die(self, killer=None):
        if not self.is_alive:
            return False

        if killer and self.duel_manager and self.duel_manager.is_player_involved(killer):
            self.duel_manager.end_duel(DuelWinner.DUEL_WINNER_KNOCKOUT, DuelComplete.DUEL_FINISHED, killer)
            self.set_health(1)
            return False

        if killer and killer.get_type_id() == ObjectTypeIds.ID_PLAYER:
            death_notify_packet = PacketWriter.get_packet(OpCode.SMSG_DEATH_NOTIFY, pack('<Q', killer.guid))
            self.enqueue_packet(death_notify_packet)

        self.pet_manager.detach_active_pet()

        TradeManager.cancel_trade(self)
        self.spirit_release_timer = 0
        self.mirror_timers_manager.stop_all()
        self.update_swimming_state(False)

        return super().die(killer)

    # override
    def respawn(self):
        # Set expected HP / Power before respawning.
        # It wasn't until Patch 0.6 that players had 50% of health and mana after reviving. It is currently unknown
        # the % that players had in 0.5.3, so 100% is assumed.
        self.set_health(self.max_health)
        if self.power_type == PowerTypes.TYPE_MANA:
            self.set_mana(self.max_power_1)
        if self.power_type == PowerTypes.TYPE_RAGE:
            self.set_rage(0)
        if self.power_type == PowerTypes.TYPE_FOCUS:
            self.set_focus(0)
        if self.power_type == PowerTypes.TYPE_ENERGY:
            self.set_energy(self.max_power_4)

        super().respawn()

        # Add Resurrection Sickness (2146) to the player.
        # TODO: Unsure if it should always be applied regardless of whether the player resurrected normally or was
        #  resurrected by another player, assuming it was always applied for now.
        self.spell_manager.handle_cast_attempt(2146, self, SpellTargetMask.SELF, validate=False)

    def repop(self):
        self.respawn()
        self.spirit_release_timer = 0
        self.teleport_deathbind()

    def get_player_bytes(self):
        return ByteUtils.bytes_to_int(
            self.player.haircolour,  # hair colour
            self.player.hairstyle,  # hair style
            self.player.face,  # player face
            self.player.skin  # player skin
        )

    def get_player_bytes_2(self):
        return ByteUtils.bytes_to_int(
            0,  # values from Exhaustion.dbc in later versions, unknown here
            self.player.bankslots,  # bank slots
            self.player.facialhair,  # facial hair
            self.player.extra_flags  # extra flags
        )

    # override
    def get_bytes_0(self):
        return ByteUtils.bytes_to_int(
            self.power_type,  # power type
            self.gender,  # gender
            self.class_,  # player class
            self.race  # player race
        )

    # override
    def get_bytes_1(self):
        return ByteUtils.bytes_to_int(
            self.sheath_state,  # sheath state
            self.shapeshift_form,  # shapeshift form
            0,  # npc flags (0 for players)
            self.stand_state  # stand state
        )

    # override
    def get_bytes_2(self):
        return ByteUtils.bytes_to_int(
            0,  # unknown
            0,  # pet flags (0 for players)
            0,  # misc flags (0 for players?)
            self.combo_points  # combo points
        )

    # override
    def get_damages(self):
        return self.damage

    def _on_relocation(self):
        for guid, unit in MapManager.get_surrounding_units(self).items():
            # Skip notify if the unit is already in combat with self, not alive or not spawned.
            if self.guid not in unit.attackers and unit.is_alive and unit.is_spawned:
                unit.notify_moved_in_line_of_sight(self)

    # override
    def on_cell_change(self):
        self.quest_manager.update_surrounding_quest_status()

    # override
    def can_attack_target(self, target):
        if not target:
            return False

        is_enemy = super().can_attack_target(target)
        if is_enemy:
            return True

        # Return True if players are friendly but dueling.
        if self.duel_manager and target is not self and self.duel_manager.is_player_involved(target):
            return self.duel_manager.duel_state == DuelState.DUEL_STATE_STARTED

        return False

    # override
    def get_type_id(self):
        return ObjectTypeIds.ID_PLAYER

    # override
    def generate_object_guid(self, low_guid):
        return low_guid | HighGuid.HIGHGUID_PLAYER

    # override
    def get_current_weapon_for_attack_type(self, attack_type: AttackTypes) -> Optional[ItemManager]:
        # Feral form attacks don't use a weapon.
        if self.is_in_feral_form():
            return None

        # Handle disarmed main hand.
        if attack_type == AttackTypes.BASE_ATTACK and self.unit_flags & UnitFlags.UNIT_FLAG_DISARMED:
            return None

        if attack_type == AttackTypes.BASE_ATTACK:
            return self.inventory.get_main_hand()
        elif attack_type == AttackTypes.OFFHAND_ATTACK:
            return self.inventory.get_offhand()
        else:
            return self.inventory.get_ranged()

    @staticmethod
    def get_team_for_race(race):
        race_entry = DbcDatabaseManager.chr_races_get_by_race(race)
        if race_entry:
            if race_entry.BaseLanguage == 1:
                return Teams.TEAM_HORDE
            elif race_entry.BaseLanguage == 7:
                return Teams.TEAM_ALLIANCE

        return Teams.TEAM_NONE
