from funsound.engine.funasr.sv.utils import SpectralCluster, merge_by_cos
from funsound.engine.base import *
from funsound.engine.funasr.sv.interface import Embedding
from funsound.brain.translator.interface import Translator
from funsound.brain.translator.languages import LANGUAGES_WHISPER
from funsound.utils import *
from funsound.config import *




class Diarization:
    def __init__(self,
                 taskId,
                 engine_sv:Embedding = None,
                 translator:Translator = None,
                 messager:Messager=None,
                 hotwords:list=[],
                 use_sv=False,
                 use_trans=False,
                 source_language=None,
                 target_language=None):
        self.taskId = taskId
        self.engine_sv = engine_sv
        self.messager = messager
        self.hotwords = hotwords
        self.use_sv = use_sv
        self.use_trans = use_trans
        self.source_language = None if not source_language else source_language
        self.target_language = target_language
        if use_trans:
            assert self.source_language in list(LANGUAGES_WHISPER.keys()) + [None]
            assert self.target_language in list(LANGUAGES_WHISPER.keys())

        self.translator = translator
        self.sr = 16000
        self.trans_task = asyncio.Queue(maxsize=5)
        self.embeddings = {}
        self.output_asr = {}
        self.output_role = {}
        self.output_trans = {}
        self.asr_finish = False

    async def sentence_generator(self,audio_data):
        """
        asr 句子生成器
        """
        pass

    async def sv_embedding(self,sentence:dict, audio_data:np.ndarray):
        """
        sv embedding
        """
        s,e = int(self.sr*sentence['start']), int(self.sr*sentence['end'])
        sentence_audio = audio_data[s:e]
        sentence_id = sentence['id']
        tmp = await self.engine_sv(sentence_audio)
        self.embeddings[sentence_id] = tmp[0][0]
        

    async def sv_cluster(self):
        """
        sv 聚类
        """
        keys, features = self.embeddings.keys(), self.embeddings.values()
        features = np.array(list(features))
        nums = features.shape[0]
        if nums<20:
            cluster_labels = np.zeros(nums, dtype="int")
        else:
            spectral_cluster = SpectralCluster()
            cluster_labels = spectral_cluster(features, None)
            cluster_labels = merge_by_cos(cluster_labels, features, cos_thr=0.78)

        dz_result = {key:int(label) for key,label in zip(keys,cluster_labels)}
        return dz_result
    

    async def _backend_trans(self):
        await asyncio.sleep(0)
        print("======== TRANSLATE:START ===========")
        batch = {}
        go = True
        while go:
            if self.trans_task.full() or self.asr_finish:
                size = self.trans_task.qsize()
                for i in range(size):
                    sentenceId, sentenceAsr = self.trans_task.get_nowait()
                    batch[str(i)] = [sentenceId, sentenceAsr]
                if self.asr_finish:
                    go = False
            
            if len(batch):
                try:
                    batch_asr = {str(i):batch[i][1] for i in batch}
                    json_str = self.translator.translate(source_language=self.source_language,
                                                        target_language=self.target_language,
                                                        content=batch_asr)
                    print(json_str)
                    if json_str:
                        json_data = json.loads(extract_json(json_str))
                        result = {batch[key][0]:json_data[key] for key in json_data}
                        self.output_trans.update(result)
                        await self.messager.send('success',msg='<TRANS>',progress=None,completed=False,result=result)
                except Exception as e:
                    traceback.print_exc()

            batch = {}
            await asyncio.sleep(0.01)
        print("======== TRANSLATE:END ===========")

    async def run(self,audio_file):

        # 后台翻译
        trans_task = None
        if self.use_trans and self.translator:
            trans_task = asyncio.create_task(self._backend_trans())

        audio_data = audio_i2f(read_audio_file(audio_file))
        audio_size = len(audio_data)
        audio_seconds = audio_size/self.sr

        # asr
        async for sentence in self.sentence_generator(audio_data):
            sentenceId = sentence['id']
            sentenceAsr = sentence['text']
            if "请不吝点赞" in  sentenceAsr:continue
            progress = sentence['end']/audio_seconds
            self.output_asr = [sentence['start'],sentence['end'],sentence['text']]
            await self.messager.send('success',msg='<ASR>',progress=progress,completed=False,result=sentence)
            # translate
            if self.use_trans and self.translator:
                await self.trans_task.put([sentenceId, sentenceAsr])
            # embedding
            if self.use_sv and self.engine_sv:
                await self.sv_embedding(sentence,audio_data)
        self.asr_finish = True
        
        if self.use_sv and self.engine_sv:
            self.output_role = await self.sv_cluster()
            await self.messager.send('success',msg='<SV>',progress=None,completed=False,result=self.output_role)
        
        if self.use_trans and self.translator and trans_task:
            await trans_task

        await self.messager.send('success',msg='<END>',progress=1.,completed=True,result=None)
    
    


