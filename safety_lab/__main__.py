import argparse

def main():
    parser=argparse.ArgumentParser(description='Agent Safety Lab：离线查看与指标重算，默认不调用模型。')
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=8899)
    args=parser.parse_args()
    import uvicorn
    uvicorn.run('safety_lab.api:app',host=args.host,port=args.port,log_level='info')

if __name__=='__main__':main()
