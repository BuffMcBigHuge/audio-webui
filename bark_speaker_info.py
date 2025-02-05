import io
import os
import tempfile
import zipfile

import numpy
import numpy as np
import gradio
import torch
import torchaudio
from bark.generation import SAMPLE_RATE, load_codec_model, preload_models
from encodec import EncodecModel
from encodec.utils import convert_audio
from scipy.io.wavfile import write as write_wav

from webui.modules.implementations.patches import bark_api, bark_custom_voices
from webui.args import args

model: EncodecModel = load_codec_model(use_gpu=not args.bark_use_cpu)



def create_custom_semantics(code):
    files = []
    data = bark_custom_voices.eval_semantics(code)
    i = 0
    for file in data:
        temp = tempfile.NamedTemporaryFile(delete=False)
        temp.name = temp.name.replace(temp.name.replace('\\', '/').split('/')[-1], f'semantic_prompt_{i}.npy')
        numpy.save(temp.name, file)
        files.append(temp.name)
        i += 1
    temp = tempfile.NamedTemporaryFile(delete=False)
    temp.name = temp.name.replace(temp.name.replace('\\', '/').split('/')[-1], f'semantic_prompts.zip')
    with zipfile.ZipFile(temp.name, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file in files:
            base_name = os.path.basename(file)
            zipf.write(file, base_name)
    return [temp.name] + files



def audio_to_semantics(file):
    f = file.name
    tensor = bark_custom_voices.wav_to_semantics(f)
    temp = tempfile.NamedTemporaryFile(delete=False)
    temp.name = temp.name.replace(temp.name.replace('\\', '/').split('/')[-1], 'semantic_prompt.npy')
    print('Semantics tensor: ', tensor)
    numpy.save(temp.name, tensor)
    return temp.name


def semantics_to_audio(file):
    f = file.name
    cpu = args.bark_use_cpu
    gpu = not cpu
    low_vram = args.bark_low_vram
    preload_models(
        text_use_gpu=gpu,
        fine_use_gpu=gpu,
        coarse_use_gpu=gpu,
        codec_use_gpu=gpu,
        fine_use_small=low_vram,
        coarse_use_small=low_vram,
        text_use_small=low_vram
    )
    if f.endswith('.npz'):
        things = numpy.load(f)
        arr = things['semantic_prompt']
        print('Semantics tensor: ', arr)
        output = bark_api.semantic_to_waveform_new(arr, decode_on_cpu=True)
        return [(SAMPLE_RATE, output), None]
    if f.endswith('.npy'):
        arr = numpy.load(f)
        print('Semantics tensor: ', arr)
        output = bark_api.semantic_to_waveform_new(arr, decode_on_cpu=True)
        return [(SAMPLE_RATE, output), None]
    if f.endswith('.zip'):
        wavs = []
        with zipfile.ZipFile(f, 'r') as zip_file:
            for file_info in zip_file.infolist():
                with zip_file.open(file_info, mode='r') as file:
                    data = numpy.load(io.BytesIO(file.read()))
                    out = bark_api.semantic_to_waveform_new(data, decode_on_cpu=True)
                    temp = tempfile.NamedTemporaryFile(delete=False)
                    temp.name = temp.name.replace(temp.name.replace('\\', '/').split('/')[-1], file_info.filename.replace('.npy', '.wav'))
                    write_wav(temp.name, SAMPLE_RATE, out)
                    wavs.append(temp.name)
        temp.name = temp.name.replace(temp.name.replace('\\', '/').split('/')[-1], 'generations.zip')
        with zipfile.ZipFile(temp.name, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file in wavs:
                base_name = os.path.basename(file)
                zipf.write(file, base_name)
        return [(SAMPLE_RATE, out), [temp.name] + wavs]  # Last as audio, rest in zip and as wavs
    return None, None


def audio_to_prompts(file):
    f = file.name
    if f.endswith('.wav'):
        fine_history, time = bark_custom_voices.generate_fine_from_wav(f)
        coarse_history = bark_custom_voices.generate_course_history(fine_history)

        fine_file = tempfile.NamedTemporaryFile(delete=False)
        fine_file.name = fine_file.name.replace(fine_file.name.replace('\\', '/').split('/')[-1], 'fine_prompt.npy')
        coarse_file = tempfile.NamedTemporaryFile(delete=False)
        coarse_file.name = coarse_file.name.replace(coarse_file.name.replace('\\', '/').split('/')[-1], 'coarse_prompt.npy')

        numpy.save(fine_file.name, fine_history)
        numpy.save(coarse_file.name, coarse_history)

        return fine_file.name, coarse_file.name
    return None, None


def codec_decode(fine_tokens):
    """Turn quantized audio codes into audio array using encodec."""
    # load models if not yet exist
    device = next(model.parameters()).device
    if fine_tokens.dtype == np.uint16:
        print('Converting uint16 (Not working yet)')
        fine_tokens = fine_tokens - 32768  # to int16
        fine_tokens = fine_tokens.astype(np.int32)
        fine_tokens = fine_tokens * 65538  # correct value to int32
    arr = torch.from_numpy(fine_tokens)[None]
    arr = arr.to(device)
    arr = arr.transpose(0, 1)
    emb = model.quantizer.decode(arr)
    out = model.decoder(emb)
    audio_arr = out.detach().cpu().numpy().squeeze()
    del arr, emb, out
    return audio_arr


def convert_to_16_bit_wav(data):
    # Based on: https://docs.scipy.org/doc/scipy/reference/generated/scipy.io.wavfile.write.html
    # Modified to support int64
    print('Converting', data.dtype)
    if data.dtype in [np.float64, np.float32, np.float16]:
        data = data / np.abs(data).max()
        data = data * 32767
        data = data.astype(np.int16)
    elif data.dtype == np.int64:
        data = data / 4295229444
        data = data.astype(np.int16)
    elif data.dtype == np.int32:
        data = data / 65538
        data = data.astype(np.int16)
    elif data.dtype == np.int16:
        pass
    elif data.dtype == np.uint16:
        data = data - 32768
        data = data.astype(np.int16)
    elif data.dtype == np.uint8:
        data = data * 257 - 32768
        data = data.astype(np.int16)
    else:
        raise ValueError(
            "Audio data cannot be converted automatically from "
            f"{data.dtype} to 16-bit int format."
        )
    return data


def file_to_audio(file):
    if file.name.endswith('.npz'):
        html = '<h1>Result</h1>'
        try:
            data = np.load(file.name)
            for dpart in data.keys():
                data_content = data[dpart]
                html += f'File name: "{dpart}"<br>' \
                        f'Shape: {data_content.shape}<br>' \
                        f'Dtype: {data_content.dtype}'
                html += '<br><br>'
            audio_arr = codec_decode(data['fine_prompt'])
            audio_arr = audio_arr
        except Exception as e:
            return None, f'<h1 style="color: red;">Error</h1>{str(e)}'
        return (SAMPLE_RATE, audio_arr), html
    elif file.name.endswith('.wav'):
        wav, sr = torchaudio.load(file.name)
        wav_pre_convert_shape = wav.shape
        wav = convert_audio(wav, sr, SAMPLE_RATE, model.channels)
        wav_post_convert_shape = wav.shape
        wav = wav.unsqueeze(0).to('cuda')
        wav_unsqueezed_shape = wav.shape
        with torch.no_grad():
            encoded_frames = model.encode(wav)
        codes = torch.cat([encoded[0] for encoded in encoded_frames], dim=-1).squeeze()
        codes_shape = codes.shape

        seconds = wav.shape[-1] / model.sample_rate

        # codes = codes.cpu().numpy()
        return (SAMPLE_RATE, wav.cpu().squeeze().numpy()), f'Seconds: {seconds}<br>' \
                                                           f'Pre convert shape: {wav_pre_convert_shape}<br>' \
                                                           f'Post convert shape: {wav_post_convert_shape}<br>' \
                                                           f'Wav unsqueezed shape: {wav_unsqueezed_shape}<br>' \
                                                           f'Codes shape: {codes_shape}<br>'


if __name__ == '__main__':
    ex = gradio.interface.Interface(fn=file_to_audio, inputs='file', outputs=['audio', 'html'])
    sg = gradio.interface.Interface(fn=semantics_to_audio, inputs='file', outputs=[gradio.Audio(), gradio.Files()])
    ats = gradio.interface.Interface(fn=audio_to_semantics, inputs='file', outputs='file')
    atp = gradio.interface.Interface(fn=audio_to_prompts, inputs='file', outputs=['file', 'file'])
    ccs = gradio.interface.Interface(fn=create_custom_semantics, inputs=gradio.TextArea(label='code', value='out = [[1, 2, 3], [4, 5, 6]]'), outputs=gradio.Files())

    gradio.TabbedInterface([ex, sg, ats, atp, ccs], ["Extraction", "Generation from semantics", "Audio to semantics", "Audio to prompts", "Semantics from code"]).launch()
