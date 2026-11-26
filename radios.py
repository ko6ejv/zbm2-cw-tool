"""Per-radio presets for the Setup Wizard: Hamlib model, CAT speed, how CW is keyed, which sound card,
and a one-paragraph hookup note. Each entry builds on a connection profile from app.PROFILES.

key_method: rigkeyer = the radio's own keyer sends CW from text (Yaesu/Elecraft KY, Icom CI-V 0x17)
            serial   = we key a COM-port line (DTR) like a straight key; radio's break-in does T/R
            audio    = we send a clean CW tone into the radio in USB / DATA mode (DigiRig style)
"""

RADIOS = {
    'Yaesu FTX-1': dict(
        profile='usbrig', rig_model=1051, cat_baud=38400, key_method='serial', key_line='DTR',
        ptt_method='VOX', min_hamlib='4.7', audio_hint=['FTX', 'USB Audio CODEC'], key_port_hint='Standard',
        note='One USB cable. CAT on the "Enhanced" COM port; CW keying on the "Standard" COM port. '
             'Radio menu: PC KEYING / RPTT SELECT = DTR, and semi break-in ON. Needs Hamlib 4.7 or newer '
             '(Rig Setup > Update Hamlib).'),
    'Yaesu FT-891': dict(
        profile='usbrig', rig_model=1036, cat_baud=4800, key_method='serial', key_line='DTR',
        ptt_method='VOX', audio_hint=['USB PnP Sound', 'USB Audio Device'], digirig_audio=True,
        key_port_hint='Standard',
        note='Radio USB = CAT ("Enhanced" COM port) + CW keying ("Standard" COM port: CW menu PC KEYING = DTR, '
             'break-in ON). The FT-891 has no USB sound card, so receive audio comes from a DigiRig (or '
             'SignaLink) on the DATA jack. CAT RATE in the menu must match the speed here.'),
    'Yaesu FT-857D': dict(
        profile='digirig_hf', rig_model=1022, cat_baud=4800,
        note='DigiRig CAT cable to the CAT jack (menu 019 CAT RATE must match the speed here), audio cable to '
             'the DATA jack. Put the radio in USB or DIG mode: CW goes out as a clean tone.'),
    'Yaesu FT-817 / FT-818': dict(
        profile='digirig_hf', rig_model=1020, cat_baud=4800,
        note='DigiRig to the ACC (CAT) and DATA jacks. Radio in USB or DIG mode for tone CW.'),
    'Yaesu FT-991A': dict(
        profile='usbrig', rig_model=1035, cat_baud=38400,
        note='One USB cable (CAT on the "Enhanced" port, USB Audio CODEC for sound). CW is sent by the '
             "radio's keyer; turn break-in ON."),
    'Elecraft KX3': dict(
        profile='usbrig', rig_model=2045, cat_baud=38400, key_method='rigkeyer', ptt_method='VOX',
        audio_hint=['USB PnP Sound', 'USB Audio Device', 'USB Audio CODEC'],
        note='KXUSB cable (or DigiRig serial) to ACC1 = CAT; CONFIG > RS232 must match the speed here. '
             "CW is sent by the KX3's own keyer (KY command): set CW break-in (VOX) ON. Receive audio: "
             'PHONES out to a USB sound card / DigiRig input.'),
    'Xiegu G90': dict(
        profile='digirig_hf', rig_model=3088, cat_baud=19200,
        note='DE-19 or DigiRig to the rear ACC port: CI-V at 19200 baud plus audio. Put the radio in USB: '
             'CW goes out as a clean tone. If CAT is flaky, Hamlib model "Xiegu X5105" also works.'),
    'Xiegu X6100': dict(
        profile='usbrig', rig_model=3087, cat_baud=19200, key_method='audio', ptt_method='CAT',
        hamlib_ptt='RIG', audio_hint=['USB Audio', 'X6100'],
        note='One USB-C cable: CI-V CAT (19200) + USB sound card. Put the radio in U-DIG (USB data): '
             'CW goes out as a clean tone, PTT by CAT.'),
    'Xiegu X6200': dict(
        profile='usbrig', rig_model=3091, cat_baud=19200, key_method='audio', ptt_method='CAT',
        hamlib_ptt='RIG', audio_hint=['USB Audio', 'X6200'],
        note='One USB-C cable: CI-V CAT (19200) + USB sound card. Put the radio in U-DIG (USB data): '
             'CW goes out as a clean tone, PTT by CAT.'),
    'Xiegu G7250': dict(
        profile='usbrig', rig_model=3091, cat_baud=19200, key_method='audio', ptt_method='CAT',
        hamlib_ptt='RIG', audio_hint=['USB Audio', 'G7250'], provisional=True,
        note='New radio - Hamlib has no G7250 entry yet, so this starts as the Xiegu X6200 (Xiegu radios '
             'speak Icom-style CI-V). If CAT does not answer, check the CI-V speed/address in the radio menu. '
             'When Hamlib adds the G7250, Rig Setup > Update Hamlib and pick it in the radio list.'),
    'Icom IC-7300': dict(
        profile='usbrig', rig_model=3073, cat_baud=115200, key_method='rigkeyer',
        audio_hint=['USB Audio CODEC'],
        note='One USB cable (CI-V + USB Audio CODEC). CI-V USB baud in the menu must match. CW is sent by '
             "the radio's keyer; set break-in to SEMI."),
    'Icom IC-705': dict(
        profile='usbrig', rig_model=3085, cat_baud=115200, key_method='rigkeyer',
        audio_hint=['USB Audio CODEC'],
        note='One USB cable (CI-V + USB Audio CODEC). CW is sent by the radio\'s keyer; break-in SEMI.'),
}
OTHER = 'Other / not listed'


def apply_radio(cfg, name, apply_profile):
    """Apply a connection profile + this radio's overrides to cfg. Returns the radio entry or None."""
    r = RADIOS.get(name)
    if not r:
        return None
    apply_profile(cfg, r['profile'])
    for k in ('rig_model', 'cat_baud', 'key_method', 'key_line', 'ptt_method', 'hamlib_ptt'):
        if k in r:
            cfg[k] = r[k]
    cfg['rig_model_name'] = name
    cfg['radio'] = name
    return r
