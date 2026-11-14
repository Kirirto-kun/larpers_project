import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
spec=importlib.util.spec_from_file_location('report',Path(__file__).parents[1]/'report.py')
report=importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)

def fixture():
    config=dict(experiment_id='test',compute_node_ids=['cpu','gpu'],workload_digest='w',input_digest='i',quality_policy='full',resource_settings={'cpu':50},unit='fragments',input_distribution='fixed',batch_policy={'size':16},concurrency=2,cold=False,git_sha='test-only',measurement_mode='normal',timing_scope='end_to_end',model_digest='m',image_digest='im',adapter_digest='a',precision='fp32',result_cache_enabled=False)
    records=[]
    for repeat in range(3):
        for kind,node,elapsed in [('standalone','cpu',2000),('standalone','gpu',1000),('pool',None,800),('single_service','gpu',1200)]:
            records.append({**config,'record_id':f'{kind}-{node}-{repeat}','kind':kind,'node_id':node,'elapsed_ms':elapsed,'results':[{'unit_id':str(i),'state':'accepted'} for i in range(10)]})
    return {'config':config,'records':records}

class Reports(unittest.TestCase):
    def test_unique_correct_units_only(self):
        self.assertEqual(report.rate({'elapsed_ms':1000,'results':[{'unit_id':'a','state':'accepted'},{'unit_id':'a','state':'accepted'},{'unit_id':'b','state':'failed'}]}),1)
    def test_exact_denominator_and_speedup(self):
        result=report.build_report(fixture())
        self.assertAlmostEqual(result['efficiency_percent'],12.5/15*100)
        self.assertEqual(result['speedup'],1.25)
    def test_missing_or_zero_baseline(self):
        for missing in (True,False):
            data=fixture()
            if missing:data['records']=[r for r in data['records'] if r['node_id']!='cpu']
            else:
                for r in data['records']:
                    if r['node_id']=='cpu':r['results']=[]
            self.assertIsNone(report.build_report(data)['efficiency_percent'])
    def test_incompatible_settings(self):
        for key,value in [('unit','images'),('concurrency',8),('input_digest','other'),('measurement_mode','fault_injection'),('timing_scope','steady_state'),('model_digest','other')]:
            data=fixture();data['records'][0][key]=value
            with self.assertRaises(ValueError):report.build_report(data)
    def test_missing_results_is_not_zero(self):
        with self.assertRaises(ValueError):report.rate({'elapsed_ms':1000})
    def test_result_cache_forbidden(self):
        data=fixture();data['config']['result_cache_enabled']=True
        with self.assertRaises(ValueError):report.build_report(data)
    def test_duplicate_pool_participant_rejected(self):
        data=fixture()
        for r in data['records']:
            if r['kind']=='pool':r['compute_node_ids']=['cpu','gpu','gpu']
        with self.assertRaises(ValueError):report.build_report(data)
    def test_extra_node_rejected(self):
        data=fixture();data['records'][0]['node_id']='coordinator'
        with self.assertRaises(ValueError):report.build_report(data)
    def test_efficiency_not_clamped(self):
        data=fixture()
        for r in data['records']:
            if r['kind']=='pool':r['elapsed_ms']=100
        result=report.build_report(data)
        self.assertGreater(result['efficiency_percent'],100)
        self.assertTrue(any('100%' in f for f in result['flags']))
    def test_bad_duration_and_duplicate_record(self):
        for value in [0,-1,float('nan'),float('inf'),True]:
            with self.assertRaises(ValueError):report.rate({'elapsed_ms':value})
        data=fixture();data['records'].append(copy.deepcopy(data['records'][0]))
        with self.assertRaises(ValueError):report.build_report(data)
    def test_exports_are_escaped_and_reproducible(self):
        data=fixture();data['bottleneck']='<script>alert(1)</script>'
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory);report.export(data,out)
            self.assertIn('&lt;script&gt;', (out/'report.html').read_text())
            self.assertEqual(len(list(out.iterdir())),4)

class ExportSafety(unittest.TestCase):
    def test_csv_node_labels_cannot_be_formulas(self):
        data=fixture();data['config']['compute_node_ids']=['=FORMULA','gpu']
        for record in data['records']:
            record['compute_node_ids']=['=FORMULA','gpu']
            if record['node_id']=='cpu':record['node_id']='=FORMULA'
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory);report.export(data,out)
            self.assertIn("'=FORMULA",(out/'rates.csv').read_text())

if __name__=='__main__':unittest.main()
