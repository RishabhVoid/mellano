"""Shared hypothetical disaster interface. Every result is derived from real geometry."""
from dataclasses import dataclass,field
import numpy as np

LABEL='Hypothetical, not a forecast'
PALETTE=['#74b879','#e5d36b','#df984b','#d95c50','#673d91']
STATE_CODES={0:'intact',1:'damaged',2:'collapsed',3:'at_risk',4:'burning',5:'burnt',6:'exposed',255:'unknown'}

@dataclass
class Frame:
    time_s:float
    severity:np.ndarray
    building_severity:np.ndarray
    building_state:np.ndarray
    depth:np.ndarray|None=None
    debris:np.ndarray|None=None
    fire:np.ndarray|None=None
    extra:dict=field(default_factory=dict)

class Plugin:
    inputs={}
    labels={}
    def apply(self,context,request):self.context=context;self.request=request;return self
    def step(self,time_s):raise NotImplementedError
    def frames(self):
        for time_s in range(0,301,10):yield self.step(time_s)
    def metrics(self,frame):return frame.extra
