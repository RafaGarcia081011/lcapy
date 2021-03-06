from . import grammar
from .parser import Parser
from .state import state

class NetfileMixin(object):

    def _init_parser(self, cpts):
        self.parser = Parser(cpts, grammar)
        # Current namespace
        self.namespace = ''
        self.subnetlists = {}
        self._anon = {}

    def _make_anon(self, kind):
        """Make identifier for anonymous component"""

        if kind not in self._anon:
            self._anon[kind] = 0
        self._anon[kind] += 1        
        return 'anon' + str(self._anon[kind])

    def _include(self, string):

        parts = string.split(' ')
        if len(parts) < 2 or parts[0] != '.include':
            raise ValueError('Expecting include filename in %s' % string)
        filename = parts[1]
        if len(parts) == 2:
            return self._netfile_add(filename, self.namespace)
        
        if len(parts) != 4 and parts[2] != 'as':
            raise ValueError('Expecting include filename as name in %s' % string)
        name = parts[3]
        namespace = self.namespace
        self.namespace = name + '.' + namespace
        self.subnetlists[self.namespace[0:-1]] = None
        ret = self._netfile_add(filename, self.namespace)        
        self.namespace = namespace
        return ret

    def _directive(self, string, namespace=''):
        
        return self.parser.cpts.Directive(self, string, namespace)
        
    def _parse(self, string, namespace=''):
        """The general form is: 'Name Np Nm symbol'
        where Np is the positive node and Nm is the negative node.

        A positive current is defined to flow from the positive node
        to the negative node.
        """

        if string.startswith('...'):
            string = string[3:].strip()
        
        if string == '':
            pass
        elif string[0] == ';':
            # Strings starting with ;; are schematic options.
            if hasattr(self, 'opts'):
                self.opts.add(string[1:])
        elif string[0:9] == '.include ':
            self._include(string)
            return None
        elif string[0:4] == '.pdb':
            import pdb; pdb.set_trace()

        cpt = self.parser.parse(string, namespace, self)
        return cpt

    def add(self, string):
        """Add a component to the netlist.
        The general form is: 'Name Np Nm args'
        where Np is the positive node and Nm is the negative node.

        A positive current is defined to flow from the positive node
        to the negative node.
        """

        # Switch context to capture new symbol definitions
        state.switch_context(self.context)
        self._add(string)
        self._invalidate()
        state.restore_context()
        
    def _add(self, string, namespace=''):
        """The general form is: 'Name Np Nm symbol'
        where Np is the positive node and Nm is the negative node.

        A positive current is defined to flow from the positive node
        to the negative node.
        """

        string = string.strip()
        if '\n' in string:
            lines = string.split('\n')
            for line in lines:
                self._add(line.strip(), namespace)
            return None

        cpt = self._parse(string, namespace)
        if cpt is not None:
            self._cpt_add(cpt)
        return cpt

    def _netfile_add(self, filename, namespace=''):
        """Add the nets from file with specified filename"""

        try:
            file = open(filename, 'r')
        except:
            try:
                file = open(filename + '.sch', 'r')
            except:
                raise FileNotFoundError('Could not open ' + filename)

        lines = file.readlines()

        state.switch_context(self.context)        
        for line in lines:
            self._add(line, namespace)
        state.restore_context()
