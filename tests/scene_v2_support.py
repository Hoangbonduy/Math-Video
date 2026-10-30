import copy,json
from src.scene_design import scene_contract as c
from src.scene_design.scene_vision import DEFAULT_REVIEW_MODEL
from tests.ai_support import FakeRouter
from src.shared.ai_config import get_model


def obj(oid,kind,x,y,w,h,label=''):
    return dict(id=oid,kind=kind,label=label,x=x,y=y,width=w,height=h,color='#56DFC5',asset_id='',points=[],overlap_with=[])


def binding(bid,kind,target,**kw):
    return dict(dict(id=bid,kind=kind,target=target,graph='',source_a='',source_b='',x_expression='',y_expression='',expression='',offset_x=0,offset_y=0,decimals=2,prefix='',suffix='',extent='segment'),**kw)


def story(scene_id='scene_1',narration='Điểm B tiến gần điểm A. Cát tuyến tiến gần tiếp tuyến.',seconds=3):
    return dict(scene_id=scene_id,narration=narration,duration=seconds,goal='Hiểu độ dốc cát tuyến khi khoảng cách giảm.',visual_strategy='Một đồ thị và một tham số khoảng cách.',beats=[dict(id='approach',start=0,end=seconds,narration_excerpt=narration,show='Đồ thị y=x², hai điểm A/B và cát tuyến.',motion='h giảm, B bám đồ thị, đường đi qua A/B và nhãn đi theo B.',understand='Độ dốc tiến đến tốc độ thay đổi tại A.',criteria=['B nằm trên đồ thị trong suốt chuyển động.','Đường cát tuyến đi qua A và B.','Giá trị độ dốc cập nhật theo h.'],required_capabilities=['point_on_graph','line_through_points','label_follow','value_label','parameters'])])


def design(scene_id='scene_1',narration=None,seconds=3):
    narration=narration or story()['narration']
    return dict(schema_version=2,scene_id=scene_id,narration=narration,purpose='Cát tuyến và độ dốc',duration=seconds,aspect_ratio=16/9,template='graph_relation',
      objects=[obj('title','text',.5,.08,.8,.08,'Cát tuyến → tiếp tuyến'),obj('curve','graph',.5,.55,.66,.64),obj('A','dot',.3,.5,.012,.022),obj('B','dot',.6,.5,.012,.022),obj('secant','line',.5,.5,0,0),obj('nameB','text',.7,.3,.10,.05,'B'),obj('slope','text',.75,.18,.4,.06,'Độ dốc')],
      groups=[],parameters=[dict(id='h',value=1,min=.1,max=1.1)],parameter_tracks=[dict(parameter='h',start=0,duration=seconds,**{'from':1,'to':.1},easing='linear')],
      graphs=[dict(id='f',object_id='curve',expression='x**2',variable='x',x_min=0,x_max=2.5,y_min=0,y_max=6.25,samples=60)],
      bindings=[binding('pointA','point_on_graph','A',graph='f',x_expression='1'),binding('pointB','point_on_graph','B',graph='f',x_expression='1+h'),binding('slopeAB','line_through_points','secant',source_a='A',source_b='B',extent='full'),binding('labelB','label_follow','nameB',source_a='B',offset_x=.025,offset_y=-.18),binding('readout','value_label','slope',expression='slopeAB',prefix='Độ dốc: ')],timeline=[],fallback_note='Minh họa vector có quan hệ toán học, không dùng ảnh ngoài.')


def vehicle(seconds=3):
    d=design(seconds=seconds);d.update(template='grouped_motion',purpose='Xe chuyển động theo nhóm',objects=[obj('body','rectangle',.35,.5,.24,.12),obj('wheel1','circle',.28,.59,.05,.08),obj('wheel2','circle',.42,.59,.05,.08),obj('name','text',.35,.4,.25,.06,'Xe')],groups=[dict(id='car',members=['body','wheel1','wheel2','name'])],parameters=[],graphs=[],bindings=[],parameter_tracks=[],timeline=[dict(object_id='car',action='move',start=0,duration=seconds,dx=.25,dy=0,value=0,easing='smooth')]);return d


class DesignRouter(FakeRouter):
    def __init__(self,review_issues=None):
        super().__init__();self.review_issues=review_issues or [];self.design_requests=[];self.stories=[]
    def complete(self,payload):
        self.requests.append(copy.deepcopy(payload));props=payload['response_format']['json_schema']['schema']['properties'];context=json.loads(payload['messages'][1]['content'])
        if 'summary' in props:
            result=dict(summary='Sửa cách thể hiện trong cảnh hiện tại.',categories=['code'],scenes=[context['snapshot']['scenes'].keys().__iter__().__next__()],resources=[],resource_updates=[],outside_scope=False)
        elif 'beats' in props:
            sc=context['scene'];result=story(sc['scene_id'],sc['narration'],context['target_seconds']);self.stories.append(result)
        elif 'queries' in props:result=dict(queries=[])
        elif 'schema_version' in props:
            sc=context['scene'];result=design(sc['scene_id'],sc['narration'],context['target_seconds']);self.design_requests.append(copy.deepcopy(context))
        elif 'layout' in props:
            result=dict(label_offsets=[],layout=[],timeline=context['design']['timeline'],parameter_tracks=context['design']['parameter_tracks'])
        else:raise AssertionError('Unexpected design phase '+repr(props.keys()))
        return dict(id='design-'+str(len(self.requests)),model=get_model(),usage=dict(prompt_tokens=100,completion_tokens=100,cost=.0001),choices=[dict(finish_reason='stop',message=dict(content=json.dumps(result,ensure_ascii=False)))])


class VisionRouter(FakeRouter):
    def __init__(self,reviews=None):super().__init__();self.reviews=reviews or [];self.model=DEFAULT_REVIEW_MODEL
    def preflight(self,search=False):return dict(super().preflight(search=search),model=self.model,scene_vision=True)
    def complete(self,payload):
        self.requests.append(copy.deepcopy(payload));context=json.loads(payload['messages'][1]['content'][0]['text']);storyboard=context['storyboard'];issues=self.reviews[min(len(self.requests)-1,len(self.reviews)-1)] if self.reviews else []
        results=[dict(beat_id=b['id'],criterion=criterion,passed=not issues,evidence='Khung hình cho thấy điểm và đường cập nhật đúng ở đầu, giữa và cuối cảnh.') for b in storyboard['beats'] for criterion in b['criteria']]
        result=dict(passed=not issues,summary='Các tiêu chí storyboard được kiểm tra qua khung hình.' if not issues else 'Cần sửa cảnh.',issues=issues,criteria_results=results)
        return dict(id='vision-'+str(len(self.requests)),model=self.model,usage=dict(prompt_tokens=100,completion_tokens=100,cost=.0001),choices=[dict(finish_reason='stop',message=dict(content=json.dumps(result,ensure_ascii=False)))])
