from faster_whisper import WhisperModel

audio = r"C:\Users\gh.erfanian\Downloads\وقتی بازار خرابه😂 -@ramiz_cafe @ramiz_cafe @ramiz_cafe #کافه #فردیس #رامیز #فان_Audio.mp3"
model = WhisperModel("medium", device="cpu", compute_type="int8")
segments, info = model.transcribe(audio, language="fa", word_timestamps=True, vad_filter=True)
print(f"language={info.language} duration={info.duration:.2f}")
for seg in segments:
    print(f"[{seg.start:.2f}-{seg.end:.2f}] {seg.text.strip()}")
    if seg.words:
        print("  " + " | ".join(f"{w.start:.2f}-{w.end:.2f}:{w.word}" for w in seg.words))
