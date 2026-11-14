"""G-Safeguard 的修正版构图、训练和推理协议。

本模块复用官方 MyGAT，不修改官方源码；下面这些属于已声明的适配：
1. A[src, dst] 与 PyG 的 edge_index[0]/[1] 分别表示发送者/接收者。
2. 节点输入是该节点截至当前轮的输出向量均值，不读取系统提示或标签。
3. 每条边使用发送者截至当前轮的广播输出，edge_attr 为 [E,T,384]。
4. 训练和推理使用同一个 GraphBuilder。训练、验证只按 question_id 隔离。
5. 标签仅标记 initial attackers，不能把它解释为全部被感染节点的真值。
6. 只以验证集损失选 checkpoint；测试数据不得用于选模型或阈值。
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import subprocess
import sys
from typing import Iterable
import uuid

import numpy as np
import torch
from torch import nn
from torch_geometric.data import Batch, Data


OFFICIAL_REPO = Path(os.environ.get('GSAFEGUARD_UPSTREAM', str(Path(__file__).resolve().parents[2] / 'vendor/G-Safeguard')))
EMBEDDING_DIM = 384
NUM_NODES = 8
PROTOCOL_VERSION = 'initial-attacker-prefix-source-edge-v1'


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def official_source_evidence() -> dict:
    """记录实际官方模型文件，不把本适配器误写成原样官方训练实现。"""
    paths = [OFFICIAL_REPO / 'PI' / fn for fn in ['model.py', 'gat_with_attr_conv.py', 'train.py', 'data.py']]
    return {
        'repository': str(OFFICIAL_REPO),
        'commit': subprocess.check_output(['git', '-C', str(OFFICIAL_REPO), 'rev-parse', 'HEAD'], text=True).strip(),
        'worktree_clean': not subprocess.check_output(['git', '-C', str(OFFICIAL_REPO), 'status', '--porcelain'], text=True).strip(),
        'source_sha256': {str(p): _sha(p) for p in paths},
    }


def _official_model_class():
    pi = OFFICIAL_REPO / 'PI'
    sys.dont_write_bytecode = True
    if str(pi) not in sys.path:
        sys.path.insert(0, str(pi))
    existing = sys.modules.get('gat_with_attr_conv')
    if existing is not None and Path(existing.__file__).resolve() != (pi / 'gat_with_attr_conv.py').resolve():
        raise RuntimeError('A different gat_with_attr_conv module is already imported.')
    name = '_gsafeguard_official_model_for_core_reproduction'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, pi / 'model.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name].MyGAT


def make_guard(max_turns: int = 4):
    """官方网络结构：384 输入、1024 隐藏、8头、2层、dropout 0.2。"""
    if max_turns < 1:
        raise ValueError('max_turns must be positive.')
    cls = _official_model_class()
    return cls(in_channels=384, hidden_channels=1024, out_channels=1,
               heads=8, concat=True, edge_dim=(max_turns,384), num_layers=2,
               dropout=0.2, residual=False, aggr_type='mean', add_time_emb=False)


class GraphBuilder:
    """只使用本机 MiniLM；encoder 参数仅供无网络机械测试注入 stub。

    生产调用不传 encoder：GraphBuilder(model_dir, cache_dir, device='cpu')。
    磁盘缓存由“实际模型指纹＋原始输出文本SHA256”确定，不加入任何标签。
    """
    def __init__(self, model_dir, cache_dir, device='cpu', *, encoder=None, encoder_id=None):
        self.model_dir = Path(model_dir).resolve()
        self.cache_dir = Path(cache_dir).resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.device = str(device)
        self.statistics = {'cache_hits':0, 'encoded_texts':0}
        if encoder is not None:
            if not encoder_id:
                raise ValueError('A test encoder requires an explicit encoder_id.')
            self.encoder = encoder
            self.model_fingerprint = hashlib.sha256(('TEST_ENCODER:' + str(encoder_id)).encode()).hexdigest()
            self.encoder_kind = 'test_stub_not_scientific_evidence'
            self.model_files = {}
        else:
            required = ['config.json','modules.json','model.safetensors','tokenizer.json', '1_Pooling/config.json']
            if not all((self.model_dir / fn).is_file() for fn in required):
                raise FileNotFoundError('Local MiniLM files are incomplete; no download or fallback encoder will be attempted.')
            fingerprint_files = required + ['config_sentence_transformers.json','sentence_bert_config.json',
                                            'tokenizer_config.json','special_tokens_map.json','vocab.txt']
            self.model_files = {fn:_sha(self.model_dir / fn) for fn in fingerprint_files if (self.model_dir / fn).is_file()}
            self.model_fingerprint = hashlib.sha256(json.dumps(self.model_files, sort_keys=True).encode()).hexdigest()
            from sentence_transformers import SentenceTransformer
            self.encoder = SentenceTransformer(str(self.model_dir), device=self.device,
                                               cache_folder=str(self.cache_dir / 'hf_runtime'),
                                               local_files_only=True)
            self.encoder.eval()
            for parameter in self.encoder.parameters():
                parameter.requires_grad_(False)
            dimension_getter = getattr(self.encoder,'get_embedding_dimension',None) or self.encoder.get_sentence_embedding_dimension
            dimension = dimension_getter()
            if dimension != EMBEDDING_DIM:
                raise ValueError(f'Expected MiniLM 384 dimensions, observed {dimension}.')
            self.encoder_kind = 'local_all-MiniLM-L6-v2'
        # 避免 Windows 260 字符路径限制；目录用短前缀，内部仍核对完整SHA256。
        self.embedding_dir = self.cache_dir / 'embeddings' / self.model_fingerprint[:16]
        self.embedding_dir.mkdir(parents=True, exist_ok=True)
        identity_path = self.embedding_dir / 'model_identity.json'
        if identity_path.exists():
            identity = json.loads(identity_path.read_text(encoding='utf-8'))
            if identity['model_fingerprint'] != self.model_fingerprint:
                raise ValueError('Embedding-cache directory fingerprint collision.')
        else:
            identity_path.write_text(json.dumps({'model_fingerprint':self.model_fingerprint,
                                                'encoder_kind':self.encoder_kind},ensure_ascii=False),encoding='utf-8')

    def metadata(self) -> dict:
        return {'protocol_version':PROTOCOL_VERSION, 'encoder_kind':self.encoder_kind,
                'model_fingerprint':self.model_fingerprint, 'model_files':self.model_files,
                'embedding_dimension':EMBEDDING_DIM, 'normalize_embeddings':False,
                'max_seq_length':getattr(self.encoder,'max_seq_length',None),
                'feature_inputs':'agent raw output text only; no question/system prompt/label',
                'node_aggregation':'mean of all available rounds including current',
                'edge_aggregation':'source broadcast history; official MyGAT mean aggregator',
                'adjacency_convention':'A[src,dst]=1', 'num_nodes':NUM_NODES,
                'label_target':'initial_attackers_only', 'cache_statistics':dict(self.statistics)}

    def _encode_texts(self, texts: list[str]) -> np.ndarray:
        vectors = {}
        missing = {}
        for text in texts:
            key = hashlib.sha256(text.encode('utf-8')).hexdigest()
            if key in vectors or key in missing:
                continue
            cache_file = self.embedding_dir / (key + '.npy')
            if cache_file.exists():
                vector = np.load(cache_file, allow_pickle=False)
                if vector.shape != (EMBEDDING_DIM,) or not np.isfinite(vector).all():
                    raise ValueError(f'Invalid cached embedding {key}.')
                vectors[key] = np.asarray(vector,dtype=np.float32)
                self.statistics['cache_hits'] += 1
            else:
                missing[key] = text
        if missing:
            with torch.inference_mode():
                encoded = self.encoder.encode(list(missing.values()), batch_size=32,
                                              show_progress_bar=False, convert_to_numpy=True,
                                              normalize_embeddings=False)
            encoded = np.asarray(encoded,dtype=np.float32)
            if encoded.shape != (len(missing),EMBEDDING_DIM) or not np.isfinite(encoded).all():
                raise ValueError('Encoder returned non-finite values or an unexpected dimension.')
            for (key,_), vector in zip(missing.items(),encoded):
                vectors[key] = vector
                temporary = self.embedding_dir / ('.tmp_' + uuid.uuid4().hex)
                with temporary.open('wb') as stream:
                    np.save(stream,vector,allow_pickle=False)
                temporary.replace(self.embedding_dir / (key + '.npy'))
                self.statistics['encoded_texts'] += 1
        return np.stack([vectors[hashlib.sha256(t.encode('utf-8')).hexdigest()] for t in texts])

    def build(self, episode: dict, phase: int | None = None, adjacency=None) -> Data:
        """phase=0 只看 R0；phase=3 只看 R0~R3，不读取未来轮文本。"""
        original = np.asarray(episode['adj_matrix'])
        adj = original if adjacency is None else np.asarray(adjacency)
        for candidate in [original,adj]:
            if candidate.shape != (NUM_NODES,NUM_NODES) or not np.isin(candidate,[0,1]).all():
                raise ValueError('Adjacency must be a binary 8 by 8 matrix.')
            if np.diag(candidate).any():
                raise ValueError('This protocol uses no self edges.')
        if np.any(adj > original):
            raise ValueError('Runtime adjacency may remove original edges, not create new ones.')
        rounds = episode['communication_data']
        if not isinstance(rounds,list) or not rounds:
            raise ValueError('An episode requires at least one communication round.')
        phase = len(rounds)-1 if phase is None else phase
        if isinstance(phase,bool) or not isinstance(phase,(int,np.integer)) or phase < 0 or phase >= len(rounds):
            raise ValueError('phase must select an existing nonnegative round.')
        selected = []
        for round_rows in rounds[:phase+1]:
            by_node = {}
            for node,text in round_rows:
                if isinstance(node,bool) or not isinstance(node,(int,np.integer)) or not 0 <= node < NUM_NODES or node in by_node:
                    raise ValueError('Each selected round must contain each of the 8 node IDs once.')
                if not isinstance(text,str) or not text.strip():
                    raise ValueError('Every selected node requires its nonempty raw output text.')
                by_node[int(node)] = text
            if set(by_node) != set(range(NUM_NODES)):
                raise ValueError('Selected round is missing nodes; do not infer a smaller graph.')
            selected.append([by_node[i] for i in range(NUM_NODES)])
        flat = [text for row in selected for text in row]
        history = self._encode_texts(flat).reshape(phase+1,NUM_NODES,EMBEDDING_DIM)
        x = history.mean(axis=0)
        source,destination = np.nonzero(adj)
        edges = np.vstack([source,destination]).astype(np.int64)
        # 广播协议下，同一发送者到不同接收者使用同一条本轮输出。
        edge_history = history[:,source,:].transpose(1,0,2)
        attackers = episode.get('attacker_idxes',[])
        if len(set(attackers)) != len(attackers) or any(isinstance(i,bool) or not isinstance(i,(int,np.integer)) or not 0 <= i < NUM_NODES for i in attackers):
            raise ValueError('Invalid initial attacker indices.')
        y = np.zeros(NUM_NODES,dtype=np.int64)
        y[list(attackers)] = 1
        data = Data(x=torch.from_numpy(np.ascontiguousarray(x)).float(),
                    edge_index=torch.from_numpy(edges).long(),
                    edge_attr=torch.from_numpy(np.ascontiguousarray(edge_history)).float(),
                    y=torch.from_numpy(y).long(), num_nodes=NUM_NODES)
        data.question_id = str(episode['question_id'])
        data.scenario = str(episode['scenario'])
        data.phase = int(phase)
        data.label_target = 'initial_attackers_only'
        data.validate(raise_on_error=True)
        return data


def binary_metrics(y_true: Iterable, scores: Iterable, threshold=0.5) -> dict:
    """分母全部明确保留；单类别集的 AUROC/AP 记为 None，而不是伪造0。"""
    from sklearn.metrics import average_precision_score, roc_auc_score
    y_raw = np.asarray(list(y_true)).reshape(-1)
    if not np.isin(y_raw,[0,1]).all():
        raise ValueError('Metrics labels must be exactly binary, not rounded or truncated.')
    y = y_raw.astype(np.int64)
    scores = np.asarray(list(scores),dtype=np.float64).reshape(-1)
    if len(y) != len(scores) or not len(y) or not np.isin(y,[0,1]).all() or not np.isfinite(scores).all() or np.any((scores<0)|(scores>1)):
        raise ValueError('Metrics require aligned binary labels and finite probabilities.')
    if not 0 <= threshold <= 1:
        raise ValueError('Invalid probability threshold.')
    predicted = scores >= threshold
    tp = int(np.sum((y==1)&predicted)); fp = int(np.sum((y==0)&predicted))
    tn = int(np.sum((y==0)&~predicted)); fn = int(np.sum((y==1)&~predicted))
    precision = tp/(tp+fp) if tp+fp else 0.0
    recall = tp/(tp+fn) if tp+fn else 0.0
    return {'threshold':float(threshold),'n_node_predictions':len(y),'positives':int(y.sum()),
            'negatives':int((y==0).sum()),'tp':tp,'fp':fp,'tn':tn,'fn':fn,
            'accuracy':(tp+tn)/len(y),'precision':precision,'recall':recall,
            'f1':2*precision*recall/(precision+recall) if precision+recall else 0.0,
            'fpr':fp/(fp+tn) if fp+tn else None,
            'auroc':float(roc_auc_score(y,scores)) if len(np.unique(y))==2 else None,
            'average_precision':float(average_precision_score(y,scores)) if y.sum() else None,
            'undefined_note':'AUROC needs both classes; AP needs positive examples; precision/recall/F1 use zero when denominator is zero.'}


def predict_guard(model,builder: GraphBuilder,episode: dict,phase=None,adjacency=None) -> list[float]:
    """永远 eval + no_grad；返回8个节点分数，不进行阈值调参。"""
    if hasattr(model,'embedding_fingerprint') and model.embedding_fingerprint != builder.model_fingerprint:
        raise ValueError('Inference encoder does not match the checkpoint embedding fingerprint.')
    device = next(model.parameters()).device
    data = builder.build(episode,phase=phase,adjacency=adjacency).to(device)
    model.eval()
    with torch.no_grad():
        scores = torch.sigmoid(model(data.x,data.edge_index,data.edge_attr).reshape(-1))
    if len(scores) != NUM_NODES or not torch.isfinite(scores).all():
        raise ValueError('Guard did not return 8 finite probabilities.')
    return scores.cpu().tolist()


def evaluate_guard(model,builder: GraphBuilder,episodes: list[dict], *, all_prefixes=True,threshold=0.5) -> dict:
    """节点指标不等于独立样本数；独立重复单位始终按 question_id 计数。"""
    rows = []
    for episode in episodes:
        phases = range(len(episode['communication_data'])) if all_prefixes else [len(episode['communication_data'])-1]
        for phase in phases:
            scores = predict_guard(model,builder,episode,phase)
            attackers = set(episode.get('attacker_idxes',[]))
            rows.extend({'question_id':str(episode['question_id']),'scenario':str(episode['scenario']),
                         'phase':phase,'node':node,'label':int(node in attackers),'score':float(score)}
                        for node,score in enumerate(scores))
    if not rows:
        raise ValueError('Cannot evaluate an empty episode list.')
    y = [r['label'] for r in rows]; scores = [r['score'] for r in rows]
    by_phase = {str(phase):binary_metrics([r['label'] for r in rows if r['phase']==phase],
                                        [r['score'] for r in rows if r['phase']==phase],threshold)
                for phase in sorted({r['phase'] for r in rows})}
    return {'label_target':'initial_attackers_only','independent_questions':len({r['question_id'] for r in rows}),
            'num_episodes':len(episodes),'all_prefixes':all_prefixes,'metrics':binary_metrics(y,scores,threshold),
            'always_normal':binary_metrics(y,[0.0]*len(y),0.5), 'by_phase':by_phase,
            'predictions':rows,'interpretation':'Rows share question, episode and agent history; do not count them as independent repetitions.'}


def _all_prefixes(episodes: list[dict],builder: GraphBuilder) -> list[Data]:
    return [builder.build(ep,phase=i) for ep in episodes for i in range(len(ep['communication_data']))]


def _batches(graphs: list[Data], *, shuffle: bool,seed: int,batch_size=32):
    """按时间长度分桶，避免把 R0 用未来填充扩成 R3 后改变均值。"""
    buckets = defaultdict(list)
    for graph in graphs:
        buckets[int(graph.edge_attr.shape[1])].append(graph)
    rng = random.Random(seed)
    batches = []
    for key in sorted(buckets):
        values = list(buckets[key])
        if shuffle:
            rng.shuffle(values)
        batches.extend(values[i:i+batch_size] for i in range(0,len(values),batch_size))
    if shuffle:
        rng.shuffle(batches)
    for values in batches:
        yield Batch.from_data_list(values)


def _epoch(model,graphs,criterion,device, *, optimizer=None,seed=0) -> dict:
    training = optimizer is not None
    model.train(training)
    loss_sum = 0.0
    count = 0
    labels = []; probabilities = []
    context = torch.enable_grad() if training else torch.no_grad()
    with context:
        for data in _batches(graphs,shuffle=training,seed=seed):
            data = data.to(device)
            if training:
                optimizer.zero_grad(set_to_none=True)
            logits = model(data.x,data.edge_index,data.edge_attr).reshape(-1)
            loss = criterion(logits,data.y.float().reshape(-1))
            if not torch.isfinite(loss):
                raise ValueError('Non-finite training/validation loss.')
            if training:
                loss.backward()
                optimizer.step()
            n = int(data.y.numel())
            loss_sum += float(loss.detach())*n
            count += n
            labels.extend(data.y.detach().cpu().tolist())
            probabilities.extend(torch.sigmoid(logits.detach()).cpu().tolist())
    return {'loss':loss_sum/count,'metrics':binary_metrics(labels,probabilities)}


def train_guard(train_episodes,val_episodes,builder: GraphBuilder,output_dir,seed=20261106,epochs=50,device='cpu') -> dict:
    """仅训练/验证数据；不接收测试集，50轮后回载验证损失最低的checkpoint。"""
    if not train_episodes or not val_episodes or epochs < 1:
        raise ValueError('Nonempty training/validation episodes and positive epochs are required.')
    train_ids = {str(ep['question_id']) for ep in train_episodes}
    val_ids = {str(ep['question_id']) for ep in val_episodes}
    if train_ids & val_ids:
        raise ValueError('Question leakage between training and validation: ' + str(sorted(train_ids & val_ids)))
    for name,episodes in [('train',train_episodes),('validation',val_episodes)]:
        keys = [(str(ep['question_id']),str(ep['scenario']),str(ep.get('replicate_id','0'))) for ep in episodes]
        if len(set(keys)) != len(keys):
            raise ValueError('Duplicate question/scenario/replicate episodes in ' + name)
    if builder.encoder_kind != 'local_all-MiniLM-L6-v2':
        raise ValueError('Formal train_guard requires verified local MiniLM; stub features cannot produce method results.')
    source_before = official_source_evidence()
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    torch.use_deterministic_algorithms(True,warn_only=True)
    train_graphs = _all_prefixes(train_episodes,builder)
    val_graphs = _all_prefixes(val_episodes,builder)
    max_turns = max(g.edge_attr.shape[1] for g in train_graphs + val_graphs)
    model = make_guard(max_turns=max_turns).to(device)
    optimizer = torch.optim.Adam(model.parameters(),lr=0.001,weight_decay=0.0002)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer,T_max=10,eta_min=1e-5)
    criterion = nn.BCEWithLogitsLoss()
    out = Path(output_dir).resolve(); out.mkdir(parents=True,exist_ok=True)
    checkpoint = out / 'guard_best.pt'
    history_path = out / 'training_history.json'
    summary_path = out / 'training_summary.json'
    history = []; best_loss = float('inf'); best_epoch = None
    architecture = {'in_channels':384,'hidden_channels':1024,'out_channels':1,'heads':8,
                    'num_layers':2,'dropout':0.2,'residual':False,'aggr_type':'mean','max_turns':int(max_turns)}
    config = {'protocol_version':PROTOCOL_VERSION,'seed':seed,'epochs':epochs,'batch_size':32,
              'temporal_batching':'separate buckets for each available prefix length',
              'lr':0.001,'weight_decay':0.0002,'optimizer':'Adam','criterion':'BCEWithLogitsLoss',
              'scheduler':{'name':'CosineAnnealingLR','T_max':10,'eta_min':1e-5},
              'checkpoint_selection':'minimum validation loss only','threshold':0.5,
              'train_question_ids':sorted(train_ids),'validation_question_ids':sorted(val_ids),
              'train_episodes':len(train_episodes),'validation_episodes':len(val_episodes),
              'train_prefix_graphs':len(train_graphs),'validation_prefix_graphs':len(val_graphs),
              'independent_unit':'question_id; node/round/paired-condition rows are correlated',
              'label_target':'initial_attackers_only','architecture':architecture,'embedding':builder.metadata(),
              'official_source':source_before,'determinism':'Seeds fixed; some CUDA scatter reductions may remain non-bitwise deterministic.'}
    (out / 'training_config.json').write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
    for epoch in range(epochs):
        learning_rate = float(optimizer.param_groups[0]['lr'])
        train = _epoch(model,train_graphs,criterion,device,optimizer=optimizer,seed=seed+epoch)
        validation = _epoch(model,val_graphs,criterion,device)
        scheduler.step()
        row = {'epoch':epoch+1,'learning_rate':learning_rate,'train':train,'validation':validation}
        history.append(row)
        if validation['loss'] < best_loss:
            best_loss = validation['loss']; best_epoch = epoch+1
            payload = {'model_state':{k:v.detach().cpu() for k,v in model.state_dict().items()},
                       'architecture':architecture,'protocol_version':PROTOCOL_VERSION,'seed':seed,
                       'selected_epoch':best_epoch,'selected_validation_loss':best_loss,
                       'embedding_fingerprint':builder.model_fingerprint,
                       'label_target':'initial_attackers_only','official_source_sha256':source_before['source_sha256']}
            temp = out / 'guard_best.pt.tmp'
            torch.save(payload,temp); temp.replace(checkpoint)
        history_path.write_text(json.dumps(history,ensure_ascii=False,indent=2),encoding='utf-8')
        print(f'Epoch {epoch+1}/{epochs}: train_loss={train["loss"]:.6f}, val_loss={validation["loss"]:.6f}, best_epoch={best_epoch}',flush=True)
    source_after = official_source_evidence()
    if source_before['source_sha256'] != source_after['source_sha256']:
        raise RuntimeError('Official model source changed during training.')
    summary = {'checkpoint':str(checkpoint),'history':str(history_path),'config':str(out / 'training_config.json'),
               'selected_epoch':best_epoch,'selected_validation_loss':best_loss,'epochs_completed':epochs,
               'train_independent_questions':len(train_ids),'validation_independent_questions':len(val_ids),
               'official_source_unchanged':True,'checkpoint_sha256':_sha(checkpoint),'builder':builder.metadata(),
               'scope':'Real-embedding adapted protocol training; not verbatim reproduction of official preprocessing.'}
    summary_path.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    summary['summary'] = str(summary_path)
    return summary


def load_guard(checkpoint,device='cpu'):
    payload = torch.load(Path(checkpoint),map_location='cpu',weights_only=True)
    if payload.get('protocol_version') != PROTOCOL_VERSION or payload.get('label_target') != 'initial_attackers_only':
        raise ValueError('Checkpoint belongs to an incompatible protocol or label target.')
    architecture = payload.get('architecture',{})
    max_turns = architecture.get('max_turns')
    if isinstance(max_turns,bool) or not isinstance(max_turns,int) or not 1 <= max_turns <= 4:
        raise ValueError('Checkpoint has an invalid temporal architecture.')
    expected = {'in_channels':384,'hidden_channels':1024,'out_channels':1,'heads':8,
                'num_layers':2,'dropout':0.2,'residual':False,'aggr_type':'mean','max_turns':max_turns}
    if architecture != expected:
        raise ValueError('Checkpoint architecture metadata does not match this protocol.')
    if payload.get('official_source_sha256') != official_source_evidence()['source_sha256']:
        raise ValueError('Checkpoint official source hashes do not match current model source.')
    model = make_guard(max_turns=max_turns)
    model.load_state_dict(payload['model_state'],strict=True)
    model.to(device).eval()
    model.embedding_fingerprint = payload['embedding_fingerprint']
    model.selected_epoch = payload['selected_epoch']
    return model
