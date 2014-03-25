#from mcircuit import Circuit
from netlist import Circuit

cct = Circuit('Voltage divider')

cct.net_add('V_s fred 0') 
cct.net_add('R_a fred bert') 
cct.net_add('R_b bert 0') 
V, I = cct.analyse()