import unittest
import numpy as np
from sim.response import Planner,position_at,water_path
from sim.disasters.base import Frame

class ResponseTests(unittest.TestCase):
    def planner(self,station_position=(5,5)):
        nodes=[{'id':'a','position':[5,5]},{'id':'b','position':[95,5]},{'id':'c','position':[95,95]}]
        edges=[{'id':'way:1:0:f','from':'a','to':'b','geometry':[[5,5],[95,5]],'travel_time_s':18},{'id':'way:2:0:f','from':'b','to':'c','geometry':[[95,5],[95,95]],'travel_time_s':18}]
        station={'id':'s','kind':'hospital','position':list(station_position),'nearest_node_id':'a','nearest_node_distance_m':float(np.linalg.norm(np.array(station_position)-[5,5])),'availability':'unknown','designated_relief_site':False}
        return Planner({'stations':[station],'facilities_status':'complete_fetched'},{'nodes':nodes,'edges':edges},(10,10),10,np.ones((10,10),bool),np.zeros((10,10),bool))
    def frame(self):
        severity=np.zeros((10,10),'uint8');severity[7:9,7:9]=4
        return Frame(0,severity,np.array([],dtype='uint8'),np.array([],dtype='uint8'))
    def test_route_uses_real_directed_geometry_and_time(self):
        result=self.planner().frame(self.frame());unit=result['units'][0]
        self.assertEqual(unit['path'],[[5.,5.],[95,5],[95,95]]);self.assertAlmostEqual(unit['eta_s'],36)
        self.assertEqual(result['metrics']['units_dispatched'],1);self.assertFalse(result['optimization']['global_optimum'])
        self.assertEqual(result['stations'][0]['availability'],'unknown')
    def test_road_closure_disconnects_station(self):
        frame=self.frame();frame.depth=np.zeros((10,10));frame.depth[0,5]=.31
        result=self.planner().frame(frame)
        self.assertEqual(result['metrics']['roads_closed'],1);self.assertTrue(result['stations'][0]['cut_off']);self.assertEqual(result['units'],[])
    def test_access_connector_cannot_cross_hazard(self):
        planner=self.planner((5,25));frame=self.frame();frame.fire=np.zeros((10,10),bool);frame.fire[1,0]=True
        result=planner.frame(frame)
        self.assertFalse(result['stations'][0]['in_zone']);self.assertTrue(result['stations'][0]['cut_off']);self.assertEqual(result['units'],[])
    def test_modelled_unit_position_interpolates_time(self):
        unit={'path':[[0,0],[10,0],[10,10]],'path_times_s':[0,10,20],'departure_time_s':5}
        self.assertEqual(position_at(unit,0),[0.,0.]);self.assertEqual(position_at(unit,20),[10.,5.]);self.assertEqual(position_at(unit,99),[10,10])
    def test_boat_refuses_dry_corner(self):
        wet=np.array([[True,False],[False,True]])
        path,reason=water_path(wet,(0,0),(1,1),10)
        self.assertIsNone(path);self.assertEqual(reason,'no_connected_water_route')
    def test_boat_connected_water_path(self):
        wet=np.ones((3,3),bool);path,reason=water_path(wet,(0,0),(2,2),10)
        self.assertIsNone(reason);self.assertEqual(path,[(0,0),(1,1),(2,2)])
    def test_missing_response_data_null_metrics(self):
        planner=Planner({}, {},(10,10),10,np.ones((10,10),bool),np.zeros((10,10),bool));result=planner.frame(self.frame())
        self.assertFalse(result['available']);self.assertIsNone(result['metrics']['roads_closed']);self.assertIsNone(result['metrics']['units_dispatched'])
    def test_hotspot_population_partial_coverage_is_null(self):
        population=np.ones((10,10));population[7,7]=np.nan
        self.assertIsNone(self.planner().hotspots(self.frame(),population)[0]['population'])
    def test_directed_graph_does_not_invent_reverse(self):
        planner=self.planner();planner.stations[0].update(position=[95,95],nearest_node_id='c',nearest_node_distance_m=0)
        frame=self.frame();frame.severity[:]=0;frame.severity[0:2,0:2]=4
        result=planner.frame(frame);self.assertEqual(result['units'],[]);self.assertEqual(result['metrics']['hotspots_unreached'],1)

if __name__=='__main__':unittest.main()
