from .base import Integration
from .alexa import AlexaIntegration
from .netcheck import NetCheckIntegration
from .sun import SunIntegration
from .adguard import AdGuardIntegration
from .pihole import PiholeIntegration
from .technitium import TechnitiumIntegration
from .calendar_ics import CalendarIntegration
from .caldav import CalDavIntegration
from .docker import DockerIntegration
from .linux_host import LinuxHostIntegration
from .proxmox import ProxmoxIntegration
from .unraid import UnraidIntegration
from .esphome import EsphomeIntegration
from .frigate import FrigateIntegration
from .homeconnect import HomeConnectIntegration
from .homekit import HomeKitIntegration
from .hydrawise import HydrawiseIntegration
from .lg_thinq import ThinQIntegration
from .hue import HueIntegration
from .kasa import KasaIntegration
from .lutron import LutronIntegration
from .meross import MerossIntegration
from .navidrome import NavidromeIntegration
from .forkast import ForkastIntegration
from .printer import PrinterIntegration
from .rachio import RachioIntegration
from .rainbird import RainBirdIntegration
from .mqtt import MqttIntegration
from .matter import MatterIntegration
from .reolink import ReolinkIntegration
from .ring import RingIntegration
from .roborock import RoborockIntegration
from .shelly import ShellyIntegration
from .sonos import SonosIntegration
from .tablets import TabletsIntegration
from .virtual import VirtualIntegration
from .weather import WeatherIntegration
from .webhooks import WebhooksIntegration

from .android_tv import AndroidTvIntegration
from .apple_tv import AppleTvIntegration
from .roku import RokuIntegration
from .samsung_tv import SamsungTvIntegration
from .vidaa_tv import VidaaTvIntegration
from .webos_tv import WebOsTvIntegration
# Order = order in the dashboard catalog.
REGISTRY: dict[str, type[Integration]] = {cls.type: cls for cls in [
    MqttIntegration, RingIntegration, ReolinkIntegration, FrigateIntegration, LutronIntegration, HueIntegration, ShellyIntegration,
    KasaIntegration, EsphomeIntegration, MerossIntegration, HomeKitIntegration, WeatherIntegration,
    CalendarIntegration, CalDavIntegration, RoborockIntegration, HomeConnectIntegration, ThinQIntegration, DockerIntegration, UnraidIntegration, LinuxHostIntegration, ProxmoxIntegration, PrinterIntegration, MatterIntegration, SonosIntegration, NavidromeIntegration, ForkastIntegration, WebhooksIntegration, TabletsIntegration,
    AdGuardIntegration, PiholeIntegration, TechnitiumIntegration,
    HydrawiseIntegration, RainBirdIntegration, RachioIntegration,
    NetCheckIntegration, SunIntegration, AlexaIntegration,
    SamsungTvIntegration, WebOsTvIntegration, AndroidTvIntegration, AppleTvIntegration, RokuIntegration,
    VidaaTvIntegration,
    VirtualIntegration,
]}
