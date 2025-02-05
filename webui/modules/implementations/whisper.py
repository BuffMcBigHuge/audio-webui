import gc
import torch
import whisper
from transformers import WhisperProcessor, WhisperForConditionalGeneration, pipeline, AutomaticSpeechRecognitionPipeline

processor: WhisperProcessor = None
model: WhisperForConditionalGeneration | AutomaticSpeechRecognitionPipeline = None
device: str = None
loaded_model: str = None


def get_official_models():
    # return [
    #     'openai/whisper-tiny.en',
    #     'openai/whisper-small.en',
    #     'openai/whisper-base.en',
    #     'openai/whisper-medium.en',
    #     'openai/whisper-tiny',
    #     'openai/whisper-small',
    #     'openai/whisper-base',
    #     'openai/whisper-medium',
    #     'openai/whisper-large',
    #     'openai/whisper-large-v2'
    # ]
    return [
        'tiny.en',
        'small.en',
        'base.en',
        'medium.en',
        'tiny',
        'small',
        'base',
        'medium',
        'large',
        'large-v2'
    ]


def unload():
    global model, processor, device, loaded_model
    model = None
    processor = None
    device = None
    loaded_model = None
    gc.collect()
    torch.cuda.empty_cache()
    return 'Unloaded'


def load(pretrained_model='openai/whisper-base', map_device='cuda' if torch.cuda.is_available() else 'cpu'):
    global model, processor, device, loaded_model
    try:
        if loaded_model != pretrained_model:
            unload()
            # model = pipeline('automatic-speech-recognition', pretrained_model, device=map_device, model_kwargs={'cache_dir': 'models/automatic-speech-recognition'})
            model = whisper.load_model(pretrained_model, map_device, 'data/models/automatic-speech-recognition/whisper')
            loaded_model = pretrained_model
            device = map_device
        return f'Loaded {pretrained_model}'
    except Exception as e:
        unload()
        return f'Failed to load, {e}'


def transcribe(wav):
    sr, wav = wav
    global model, processor, device, loaded_model
    if loaded_model is not None:
        import traceback
        try:
            if sr != 16000:
                import torchaudio.functional as F
                wav = F.resample((torch.tensor(wav).to(device).float() / 32767.0).mean(-1).squeeze().unsqueeze(0), sr, 16000).flatten().cpu().detach().numpy()
                sr = 16000
            # return model(wav)['text'].strip()
            return whisper.transcribe(model, wav)['text'].strip()
        except Exception as e:
            traceback.print_exception(e)
            return f'Exception: {e}'
    else:
        return 'No model loaded! Please load a model.'
