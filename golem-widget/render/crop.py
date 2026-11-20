import os, json
from PIL import Image
info=json.load(open('frames-info.json'))
os.system('rm -rf out && mkdir -p out/frames')
meta={}
for name,c in info.items():
    fs=sorted(f for f in os.listdir(f'raw/{name}') if f.endswith('.png'))
    os.makedirs(f'out/frames/{name}')
    Image.open(f'raw/{name}/bg.jpg').save(f'out/frames/{name}/bg.jpg',quality=85)
    rects=[]; tot=0
    for i,f in enumerate(fs):
        im=Image.open(f'raw/{name}/{f}'); W,H=im.size
        b=im.getchannel('A').point(lambda v:255 if v>6 else 0).getbbox() or (0,0,2,2)
        x0,y0,x1,y1=max(0,b[0]-1),max(0,b[1]-1),min(W,b[2]+1),min(H,b[3]+1)
        im.crop((x0,y0,x1,y1)).quantize(colors=200,method=Image.Quantize.FASTOCTREE).save(f'out/frames/{name}/{i:03d}.png',optimize=True)
        rects.append([round(x0/W,4),round(y0/H,4),round((x1-x0)/W,4),round((y1-y0)/H,4)])
        tot+=(x1-x0)*(y1-y0)*4
    meta[name]={'n':len(fs),'dur':c['dur'],'r':rects}
    print(name,len(fs),'decoded MB 24fps:',round(tot/1e6,1))
json.dump(meta,open('out/meta.json','w'),separators=(',',':'))
