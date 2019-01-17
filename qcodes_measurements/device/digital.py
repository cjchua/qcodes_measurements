"""
Support functions for digital gates
"""
from functools import partial
import enum

from qcodes import Instrument, InstrumentChannel, ChannelList, Parameter
from qcodes.utils.validators import Numbers, Bool, MultiType, Enum

try:
    import MDAC
except ModuleNotFoundError:
    class _Blank():
        MDACChannel = type(None)
        MDAC = type(None)
    MDAC = _Blank()
from .bb import BBChan

from .gate import GateWrapper, MDACGateWrapper, BBGateWrapper
from .device import Device

class DigitalMode(str, enum.Enum):
    """
    Analog to ConnState with states for digital logic

    Note: HIGH/LOW/GND cause the gate value to be locked, and will not
    allow changes through a set
    """
    IN = enum.auto() # Connect SMC, Disconnect DAC
    OUT = enum.auto() # Disconnect SMC, Connect DAC
    PROBE_OUT = enum.auto() # Connect SMC, Connect DAC
    HIGH = enum.auto()
    LOW = enum.auto()
    GND = enum.auto()

DigitalMode.OUTPUT_MODES = (DigitalMode.OUT, DigitalMode.PROBE_OUT, DigitalMode.HIGH,
                            DigitalMode.LOW)
DigitalMode.INPUT_MODES = (DigitalMode.IN, DigitalMode.GND)

class DigitalGate(Parameter):
    """
    Represents a digital gate, i.e. one that has two possible values, v_high and v_low.
    This will usually be part of a DigitalDevice which will control the values
    of v_high/v_low as parameters.
    Parameters:
        source: The voltage source
        name: Gate name
        v_high: high voltage level
        v_low: low voltage level
        v_hist: range around v_high/v_low around which a high/low value will be read
    """
    def __init__(self, name, source, v_high, v_low, v_hist=0.2, label=None,
                 io_mode=DigitalMode.OUT, **kwargs):
        # Check that the source is a valid voltage source
        if not isinstance(source, (Instrument, InstrumentChannel)):
            raise TypeError("The source must be an instrument or instrument channel.")
        if not hasattr(source, "voltage") or not hasattr(source.voltage, "set"):
            raise TypeError("The source for a gate must be able to set a voltage")

        if label is None:
            label = name

        # Initialize the parameter
        super().__init__(name=name,
                         label=label,
                         unit="V",
                         vals=MultiType(Bool(), Numbers()))
        self.source = source
        self._v_high = v_high
        self._v_low = v_low
        self.v_hist = v_hist
        self.io_mode = io_mode

        # If a gate is locked, it's value won't be changed
        self.lock = False

    @property
    def v_high(self):
        return self._v_high
    @v_high.setter
    def v_high(self, val):
        self._v_high = val
        lock = self.lock
        self.lock = False
        if self.io_mode in DigitalMode.OUTPUT_MODES:
            self(self())
        self.lock = lock

    @property
    def v_low(self):
        return self._v_low
    @v_low.setter
    def v_low(self, val):
        self._v_low = val
        lock = self.lock
        self.lock = False
        if self.io_mode in DigitalMode.OUTPUT_MODES:
            self(self())
        self.lock = lock

    def get_raw(self):
        """
        Return the state of the gate if within the defined setpoints, otherwise return 0
        """
        voltage = self.source.voltage()
        if abs(voltage - self.v_high) < self.v_hist:
            return 1
        elif abs(voltage - self.v_low) < self.v_hist:
            return 0
        return -1

    def set_raw(self, value):
        """
        Set the output of this digital gate, unless the gate is locked, in which case don't do
        anything
        """
        if self.lock: # Don't change value if the gate is locked.
            return
        if value:
            self.source.voltage(self.v_high)
        else:
            self.source.voltage(self.v_low)

class DigitalGateWrapper(GateWrapper):
    """
    Digital gate wrapper, which allows set/get of state

    Note: The accesses for various attributes can be confusing here:
        - self.gate - The underlying DigitalGate
        - self.parent - The underlying DAC/BB channel
    """
    def __init__(self, parent, name):
        super().__init__(parent, name, GateType=DigitalGate)
        self.add_parameter("out",
                           get_cmd=parent,
                           set_cmd=parent,
                           vals=self.gate.vals)

        self.add_parameter("io_mode",
                           get_cmd=lambda: str(self.gate.io_mode),
                           set_cmd=self._set_io_mode,
                           vals=Enum(*DigitalMode))

        self.add_parameter("lock",
                           get_cmd=partial(getattr, self.gate, "lock"),
                           set_cmd=partial(setattr, self.gate, "lock"),
                           vals=Bool())

        self.add_parameter("v_high",
                           get_cmd=partial(getattr, self.gate, "v_high"),
                           set_cmd=partial(setattr, self.gate, "v_high"),
                           vals=self.parent.voltage.vals)

        self.add_parameter("v_low",
                           get_cmd=partial(getattr, self.gate, "v_low"),
                           set_cmd=partial(setattr, self.gate, "v_low"),
                           vals=self.parent.voltage.vals)

        # Note: we override the voltage parameter here, since by default the GateWrapper
        # pulls the voltage from self.gate, which for a digital gate returns 0/1
        self.parameters['voltage'] = self.parent.voltage

    def _set_io_mode(self, val):
        self.lock(False)
        if val == DigitalMode.IN:
            self.open()
        elif val == DigitalMode.OUT:
            self.dac()
        elif val == DigitalMode.PROBE_OUT:
            self.probe()
        elif val == DigitalMode.HIGH:
            self.dac()
            self.out(1)
            self.lock(True)
        elif val == DigitalMode.LOW:
            self.dac()
            self.out(0)
            self.lock(True)
        elif val == DigitalMode.GND:
            self.ground()
        self.gate.io_mode = val

class MDACDigitalGateWrapper(DigitalGateWrapper, MDACGateWrapper):
    """
    Digital gate wrapper of an MDAC, which allows set/get of state
    """
    def __init__(self, parent, name):
        super().__init__(parent, name)
        self.parent.filter.vals = Enum(0, 1, 2)
        self.parent.filter(0)

class BBDigitalGateWrapper(DigitalGateWrapper, BBGateWrapper):
    """
    Digital gate wrapper of an BB, which allows set/get of state
    """

class DigitalDevice(Device):
    """
    Device which expects digital control as well as potential analog
    voltages
    """
    def __init__(self, name):
        super().__init__(name)

        # Add digital gates to the device
        digital_gates = ChannelList(self, "digital_gates", DigitalGateWrapper)
        self.add_submodule("digital_gates", digital_gates)

        # Add digital parameters
        self._v_high = 1.8
        self._v_low = 0
        self.add_parameter("v_high",
                           initial_value=1.8,
                           get_cmd=partial(getattr, self, "_v_high"),
                           set_cmd=self._update_vhigh,
                           vals=Numbers())
        self.add_parameter("v_low",
                           initial_value=0,
                           get_cmd=partial(getattr, self, "_v_low"),
                           set_cmd=self._update_vlow,
                           vals=Numbers())

    def add_digital_gate(self, name, source, io_mode=DigitalMode.OUT, **kwargs):
        self.add_parameter(name, parameter_class=DigitalGate, source=source,
                           v_high=self.v_high(), v_low=self.v_low(), io_mode=io_mode,
                           **kwargs)
        gate = self.get_channel_controller(self.parameters[name])
        gate.io_mode(io_mode)

    def add_parameter(self, name, parameter_class=Parameter, **kwargs):
        super().add_parameter(name, parameter_class, **kwargs)
        new_param = self.parameters[name]

        if isinstance(new_param, DigitalGate):
            if isinstance(new_param.source, MDAC.MDACChannel):
                self.digital_gates.append(MDACDigitalGateWrapper(new_param, name))
            elif isinstance(new_param.source, BBChan):
                self.digital_gates.append(BBDigitalGateWrapper(new_param, name))
            else:
                self.digital_gates.append(DigitalGateWrapper(new_param, name))

    def _update_vhigh(self, new_val):
        for gate in self.digital_gates:
            gate.v_high(new_val)
        self._v_high = new_val
    def _update_vlow(self, new_val):
        for gate in self.digital_gates:
            gate.v_low(new_val)
        self._v_low = new_val

    def get_channel_controller(self, param):
        """
        Return the channel controller for a given parameter
        """
        if isinstance(param, DigitalGate):
            return getattr(self.digital_gates, param.name)
        return super().get_channel_controller(param)
