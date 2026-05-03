#!/usr/bin/env python3
"""修复归档 JSON：排序(含同分反推) + 标签重命名"""

import json
import os

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')


def fix_tournament(t):
    """修复单个赛事的所有轮次"""
    rounds = t['rounds']
    changes = []

    # 从后往前处理：用下一轮选手集合来确定当前轮同分排序
    for ri in range(len(rounds) - 1, -1, -1):
        curr = rounds[ri]
        nxt = rounds[ri + 1] if ri + 1 < len(rounds) else None

        # 收集下一轮所有选手名字
        next_all_names = set()
        if nxt:
            for ng in nxt['groups']:
                for p in ng['players']:
                    next_all_names.add(p['name'])

        for g in curr['groups']:
            players = g['players']

            # 先按 totalPoints 降序排
            players.sort(key=lambda p: -p['totalPoints'])

            # 同分时：晋级者排前面
            i = 0
            while i < len(players):
                j = i + 1
                while j < len(players) and players[j]['totalPoints'] == players[i]['totalPoints']:
                    j += 1
                if j > i + 1:
                    tied = players[i:j]
                    tied.sort(key=lambda p: (0 if p['name'] in next_all_names else 1))
                    players[i:j] = tied
                i = j

    # 标签重命名
    for r in rounds:
        is_final = (len(r['groups']) == 1 and len(rounds) > 1)
        for i, g in enumerate(r['groups']):
            old_label = g['label']
            if is_final:
                g['label'] = '决赛'
            else:
                g['label'] = f'{i + 1}组'
            if old_label != g['label']:
                changes.append(f"标签 {r['label']} {old_label} → {g['label']}")

    return changes


def fix_index_entry(entry, data):
    """同步修复 index.json 中的 rounds 信息"""
    for t in data['tournaments']:
        if t['name'] == entry['name']:
            entry['rounds'] = []
            for r in t['rounds']:
                entry['rounds'].append({
                    'label': r['label'],
                    'groupCount': len(r['groups'])
                })
            break


def main():
    total_changes = 0

    for fname in sorted(os.listdir(DATA_DIR)):
        if not fname.endswith('.json') or fname in ('index.json', '.gitkeep'):
            continue

        fpath = os.path.join(DATA_DIR, fname)
        with open(fpath, 'r', encoding='utf-8') as f:
            data = json.load(f)

        file_changes = []
        for t in data['tournaments']:
            changes = fix_tournament(t)
            file_changes.extend(changes)

        if file_changes:
            with open(fpath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f'✅ {fname}: {len(file_changes)} 处修改')
            for c in file_changes:
                print(f'   {c}')
            total_changes += len(file_changes)
        else:
            print(f'⏭️ {fname}: 无需修改')

    # 同步 index.json
    index_path = os.path.join(DATA_DIR, 'index.json')
    with open(index_path, 'r', encoding='utf-8') as f:
        index = json.load(f)

    for entry in index['tournaments']:
        json_path = os.path.join(DATA_DIR, f'{entry["filename"]}.json')
        if os.path.exists(json_path):
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            fix_index_entry(entry, data)

    with open(index_path, 'w', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    print(f'\n共修改 {total_changes} 处，index.json 已同步')


if __name__ == '__main__':
    main()
