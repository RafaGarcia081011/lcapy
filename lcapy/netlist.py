"""This module provides circuit analysis using modified nodal analysis
(MNA).

The circuit is described using netlists, similar to SPICE, but with
arbitrary node names (except for the ground node which is labelled 0).
The netlists can be loaded from a file or created at run-time.  For
example:

>>> from lcapy import Circuit
>>> cct = Circuit('Voltage divider')
>>> cct.add('V_s fred 0')
>>> cct.add('R_a fred 1')
>>> cct.add('R_b 1 0')

Branch currents and branch voltage differences can be found using the
component name as an attribute, for example,

>>> cct.V_s.V.pprint()
>>> cct.R_a.I.pprint()

Nodal voltages (with respect to the ground node) can be found using
the node name or number as index, for example,

>>> cct['fred'].V.pprint()
>>> cct[1].V.pprint()


Copyright 2014, 2015 Michael Hayes, UCECE

"""

# TODO: Add option to defer evaluation and thus keep things symbolic.
# This will help to simplify results that are not cancelled due to
# numerical quantisation.

from __future__ import division
from lcapy.core import pprint, Hs, Vs, Zs, Ys, Expr, tsym
from lcapy.core import s, j, omega, uppercase_name
#from lcapy.oneport import V, I, v, i, Vdc, Idc, Vac, Iac, Vstep, Istep
#from lcapy.oneport import Vacstep, Iacstep
#from lcapy.oneport import R, L, C, G, Y, Z
from lcapy.twoport import AMatrix, TwoPortBModel
from schematic import Schematic, Opts, SchematicOpts
from mna import MNA
import grammar
from parser import Parser
import mnacpts as cpts
import re
from copy import copy


__all__ = ('Circuit', )

parser = Parser(cpts, grammar)

class Ldict(dict):

    """Lazy dictionary for inverse Laplace"""

    def __init__(self, Vdict):

        super(Ldict, self).__init__()

        self.Vdict = Vdict


    def __getitem__(self, key):

        # If key is an integer, convert to a string.
        if isinstance(key, int):
            key = '%d' % key
        
        # Note, need to use keys method to catch branch names.
        if (key not in self) and (key in self.Vdict.keys()):
            v = self.Vdict[key].inverse_laplace()
            self[key] = v
            return v

        return super(Ldict, self).__getitem__(key)

    def keys(self):

        return self.Vdict.keys()


class Node(object):

    def __init__(self, cct, name):

        self.cct = cct
        self.name = name
        self.pos = None
        self.port = False
        parts = name.split('_')
        self.rootname = parts[0] if name[0] != '_' else name
        self.primary = len(parts) == 1
        # List of elements connected to this node.
        self.list = []

    @property
    def V(self):
        """Node voltage with respect to ground"""

        return self.cct.V[self.name]

    @property
    def v(self):
        """Node time-domain voltage with respect to ground"""

        return self.cct.v[self.name]

    def append(self, elt):

        if elt.type in ('P', ):
            self.port = True

        self.list.append(elt)


class Netlist(MNA):

    def __init__(self, filename=None):

        self.anon = {}
        self.elements = {}
        # Independent current and voltage sources.  This does not include
        # implicit sources due to initial conditions.
        self.independent_sources = {}
        self.nodes = {}
        # Shared nodes (with same voltage)
        self.snodes = {}

        self.opts = SchematicOpts()

        if filename is not None:
            self.netfile_add(filename)

    def __getitem__(self, name):
        """Return element or node by name"""

        # If name is an integer, convert to a string.
        if isinstance(name, int):
            name = '%d' % name

        if name in self.nodes:
            return self.nodes[name]

        if name in self.elements:
            return self.elements[name]

        # Try first anonymous name.
        if name + '#1' in self.elements:
            return self.elements[name + '#1']

        raise AttributeError('Unknown element or node name %s' % name)


    def __getattr__(self, attr):
        """Return element or node by name"""

        # This gets called if there is no explicit attribute attr for
        # this instance.  This is primarily for accessing elements
        # and non-numerical node names.

        return self.__getitem__(attr)


    def netfile_add(self, filename):
        """Add the nets from file with specified filename"""

        file = open(filename, 'r')

        lines = file.readlines()

        for line in lines:
            self.add(line)

    def netlist(self):
        """Return the current netlist"""

        lines = ''
        for key, elt in self.elements.iteritems():
            new_elt = copy(elt)

            line = new_elt.__str__()
            optstr = new_elt.opts.format()
            if optstr != '':
                line += ' ; ' + optstr

            lines += line + '\n'

        return lines

    def _node_add(self, node, elt):

        if node not in self.nodes:
            self.nodes[node] = Node(self, node)
        self.nodes[node].append(elt)

        vnode = self.nodes[node].rootname

        if vnode not in self.snodes:
            self.snodes[vnode] = []

        if node not in self.snodes[vnode]:
            self.snodes[vnode].append(node)

    def _invalidate(self):

        for attr in ('_sch', '_A', '_V'):
            if hasattr(self, attr):
                delattr(self, attr)

    def parse(self, string):
        """The general form is: 'Name Np Nm symbol'
        where Np is the positive node and Nm is the negative node.

        A positive current is defined to flow from the positive node
        to the negative node.
        """

        if '\n' in string:
            lines = string.split('\n')
            for line in lines:
                self.add(line.strip())
            return

        cpt = parser.parse(string, self)
        if cpt is None:
            return

        opts = Opts(cpt.opts_string)
        cpt.opts = opts
        return cpt

    def _elt_add(self, elt):

        if elt.name in self.elements:
            print('Overriding component %s' % elt.name)
            # Need to search lists and update component.
        else:
            # Check that this name won't conflict with an attr.
            # For example, cannot have name V or I.  Perhaps
            # rename these attributes?
            if hasattr(self, elt.name):
                raise ValueError('Invalid component name %s' % elt.name)

        self.elements[elt.name] = elt

        if elt.type in ('V', 'I'):
            self.independent_sources[elt.name] = elt

        for node in elt.nodes:
            self._node_add(node, elt)

    def add(self, string, *args):
        """Add a component to the netlist.
        The general form is: 'Name Np Nm args'
        where Np is the positive node and Nm is the negative node.

        A positive current is defined to flow from the positive node
        to the negative node.
        """

        elt = self.parse(string)
        if elt is None:
            return

        self._invalidate()
        self._elt_add(elt)

    def net_add(self, string, *args):
        """Add a component to the netlist.
        The general form is: 'Name Np Nm args'
        where Np is the positive node and Nm is the negative node.

        A positive current is defined to flow from the positive node
        to the negative node.

        Note, this method has been superseded by add.
        """

        self.add(string, *args)

    def remove(self, name):
        """Remove specified element"""

        self._invalidate()

        if name not in self.elements:
            raise ValueError('Unknown component: ' + name)
        self.elements.pop(name)

    def _make_open(self, node1, node2, opts):
        """Create a dummy open-circuit"""

        opts.strip_current_labels()
        opts.strip_labels()

        net = 'O %s %s ; %s' % (node1, node2, opts.format())
        return self.parse(net)

    def _make_short(self, node1, node2, opts):
        """Create a dummy short-circuit"""

        opts.strip_voltage_labels()
        opts.strip_labels()

        net = 'W %s %s ; %s' % (node1, node2, opts.format())
        return self.parse(net)

    def _make_Z(self, name, node1, node2, value, opts):
        """Create a dummy impedance"""

        net = 'Z%s %s %s "%s"; %s' % (
            name, node1, node2, value, opts.format())
        return self.parse(net)

    def _make_V(self, node1, node2, value, opts):
        """Create a dummy s-domain voltage source"""

        net = 'V %s %s "%s"; %s' % (node1, node2, value, opts.format())
        return self.parse(net)

    def _make_I(self, node1, node2, value, opts):
        """Create a dummy s-domain current source"""

        net = 'I %s %s "%s"; %s' % (node1, node2, value, opts.format())
        return self.parse(net)

    @property
    def v(self):
        """Return dictionary of t-domain node voltages indexed by node name
        and voltage differences indexed by branch name"""

        if not hasattr(self, '_vcache'):
            self._vcache = Ldict(self.V)

        return self._vcache

    @property
    def i(self):
        """Return dictionary of t-domain branch currents indexed
        by component name"""

        if not hasattr(self, '_icache'):
            self._icache = Ldict(self.I)

        return self._icache

    def Voc(self, Np, Nm):
        """Return open-circuit s-domain voltage between nodes Np and Nm."""

        return self.V[Np] - self.V[Nm]

    def voc(self, Np, Nm):
        """Return open-circuit t-domain voltage between nodes Np and Nm."""

        return self.Voc(Np, Nm).inverse_laplace()

    def Isc(self, Np, Nm):
        """Return short-circuit s-domain current between nodes Np and Nm."""

        self.add('Vshort_ %d %d 0' % (Np, Nm))

        Isc = self.Vshort_.I
        self.remove('Vshort_')

        return Isc

    def isc(self, Np, Nm):
        """Return short-circuit t-domain current between nodes Np and Nm."""

        return self.Isc(Np, Nm).inverse_laplace()

    def thevenin(self, Np, Nm):
        """Return Thevenin model between nodes Np and Nm"""

        Voc = self.Voc(Np, Nm)

        return V(Voc) + Z(self.impedance(Np, Nm))

    def norton(self, Np, Nm):
        """Return Norton model between nodes Np and Nm"""

        Isc = self.Isc(Np, Nm)

        return I(Isc) | Y(self.admittance(Np, Nm))

    def admittance(self, Np, Nm):
        """Return admittance between nodes Np and Nm with independent 
        sources killed.

        """

        new = self.kill()

        # Connect 1 V s-domain voltage source between nodes and
        # measure current.
        new.add('Vin_ %d %d {s * 0 + 1}' % (Np, Nm))
        If = -new.Vin_.I
        new.remove('Vin_')

        return Ys(If)

    def impedance(self, Np, Nm):
        """Return impedance between nodes Np and Nm with independent
        sources killed.

        """

        new = self.kill()

        # Connect 1 A s-domain current source between nodes and
        # measure voltage.
        new.add('Iin_ %d %d {s * 0 + 1}' % (Np, Nm))
        Vf = new.Voc(Np, Nm)
        new.remove('Iin_')

        return Zs(Vf)

    def Y(self, Np, Nm):
        """Return admittance between nodes Np and Nm with independent
        sources killed.

        """

        return self.admittance(Np, Nm)

    def Z(self, Np, Nm):
        """Return impedance between nodes Np and Nm with independent
        sources killed.

        """

        return self.impedance(Np, Nm)

    def transfer(self, N1p, N1m, N2p, N2m):
        """Create voltage transfer function V2 / V1 where:
        V1 is V[N1p] - V[N1m]
        V2 is V[N2p] - V[N2m]

        Note, independent sources are killed."""

        new = self.kill()
        new.add('V1_ %d %d impulse' % (N1p, N1m))

        H = Hs(new.Voc(N2p, N2m) / new.V1_.V)

        return H

    def Amatrix(self, N1p, N1m, N2p, N2m):
        """Create A matrix from network, where:
        I1 is the current flowing into N1p and out of N1m
        I2 is the current flowing into N2p and out of N2m
        V1 is V[N1p] - V[N1m]
        V2 is V[N2p] - V[N2m]
        """

        if self.Voc(N1p, N1m) != 0 or self.Voc(N2p, N2m) != 0:
            raise ValueError('Network contains independent sources')

        try:

            self.add('V1_ %d %d impulse' % (N1p, N1m))

            # A11 = V1 / V2 with I2 = 0
            # Apply V1 and measure V2 with port 2 open-circuit
            A11 = Hs(self.V1_.V / self.Voc(N2p, N2m))

            # A12 = V1 / I2 with V2 = 0
            # Apply V1 and measure I2 with port 2 short-circuit
            A12 = Zs(self.V1_.V / self.Isc(N2p, N2m))

            self.remove('V1_')

            self.add('I1_ %d %d impulse' % (N1p, N1m))

            # A21 = I1 / V2 with I2 = 0
            # Apply I1 and measure I2 with port 2 open-circuit
            A21 = Ys(-self.I['I1_'] / self.Voc(N2p, N2m))

            # A22 = I1 / I2 with V2 = 0
            # Apply I1 and measure I2 with port 2 short-circuit
            A22 = Hs(-self.I['I1_'] / self.Isc(N2p, N2m))

            self.remove('I1_')
            return AMatrix(A11, A12, A21, A22)

        except ValueError:
            raise ValueError('Cannot create A matrix')

    def _kill(self, sourcenames):

        new = Circuit()
        new.opts = copy(self.opts)

        for key, elt in self.elements.iteritems():
            if key in sourcenames:
                net = elt.kill()
            else:
                net = elt.kill_initial()
            new.add(net)
        return new        

    def kill_except(self, *args):
        """Return a new circuit with all but the specified sources killed;
        i.e., make the voltage sources short-circuits and the current
        sources open-circuits.  If no sources are specified, all
        independent sources (including initial conditions) are killed.

        """

        for arg in args:
            if arg not in self.independent_sources:
                raise ValueError('Element %s is not a known source' % arg)
        sources = []
        for key, source in self.independent_sources.iteritems():
            if key not in args:
                sources.append(key)
        return self._kill(sources)

    def kill(self, *args):
        """Return a new circuit with the specified sources killed; i.e., make
        the voltage sources short-circuits and the current sources
        open-circuits.  If no sources are specified, all independent
        sources (including initial conditions) are killed.

        """

        if len(args) == 0:
            return self.kill_except()

        sources = []
        for arg in args:
            if arg not in self.independent_sources:
                raise ValueError('Element %s is not a known source' % arg)
            sources.append(self.independent_sources[arg].name)

        return self._kill(sources)

    def twoport(self, N1p, N1m, N2p, N2m):
        """Create twoport model from network, where:
        I1 is the current flowing into N1p and out of N1m
        I2 is the current flowing into N2p and out of N2m
        V1 is V[N1p] - V[N1m]
        V2 is V[N2p] - V[N2m]
        """

        V2b = self.Voc(N2p, N2m)
        I2b = self.Isc(N2p, N2m)

        A = self.kill().Amatrix(N1p, N1m, N2p, N2m)

        return TwoPortBModel(A.B, V2b, I2b)

    @property
    def sch(self):

        if hasattr(self, '_sch'):
            return self._sch

        netlist = self.netlist()

        sch = Schematic()

        for net in netlist.split('\n')[0:-1]:
            sch.add(net)

        self._sch = sch
        return sch

    def pre_initial_model(self):
        """Generate circuit model for determining the pre-initial
        conditions."""

        new_cct = self.__class__()

        for key, elt in self.elements.iteritems():

            # Assume initial C voltage and L current is zero.

            if elt.type in ('V', 'I', 'Vac', 'Iac'):
                print('Cannot determine pre-initial condition for %s'
                      ', assuming 0' % elt.name)

            if elt.type in ('C', 'Istep', 'Iacstep', 'I', 'Iac', 'Iimpulse'):
                elt = self._make_open(elt.nodes[0], elt.nodes[1], elt.opts)
            elif elt.type in ('L', 'Vstep', 'Vacstep', 'V',
                                  'Vac', 'Vimpulse'):
                elt = self._make_short(elt.nodes[0], elt.nodes[1], elt.opts)
            new_elt = copy(elt)             
            new_elt.cct = new_cct
            new_cct._elt_add(new_elt)

        return new_cct

    def s_model(self, var=s):

        new = Circuit()
        new.opts = copy(self.opts)

        for key, elt in self.elements.iteritems():
            net = elt.s_model(var)
            new.add(net)
        return new        

    def ac_model(self):
        return self.s_model(j * omega)

    def draw(self, filename=None, **kwargs):

        cct = self
        if kwargs.pop('s_model', False):
            cct = cct.s_model()

        return cct.sch.draw(filename=filename, opts=self.opts, **kwargs)


class Circuit(Netlist):

    def __init__(self, filename=None):

        super(Circuit, self).__init__(filename)


def test():

    cct = Circuit('Test')

    cct.add('V_s fred 0')
    cct.add('R_a fred bert')
    cct.add('R_b bert 0')

    pprint(cct.V)

    pprint(cct.I)

    return cct
