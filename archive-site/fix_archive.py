#!/usr/bin/env python3
"""修复归档 JSON：排序 + 晋级对齐 + 标签重命名"""

import json
import os

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')


def fix_tournament(t):
    """修复单个赛事的所有轮次"""
    rounds = t['rounds']
    changes = []

    # ── 第一步：每组按 totalPoints 降序排序 ──
    for r in rounds:
        for g in r['groups']:
            old_order = [p['name'] for p in g['players']]
            g['players'].sort(key=lambda p: p['totalPoints'], reverse=True)
            new_order = [p['name'] for p in g['players']]
            if old_order != new_order:
                changes.append(f"排序 {r['label']} {g['label']}")

    # ── 第二步：修正 nextRoundGroupId（用选手名字反推）──
    for ri in range(len(rounds) - 1):
        curr = rounds[ri]
        nxt = rounds[ri + 1]

        # 建立下一轮每组的选手集合
        next_group_players = {}
        for ng in nxt['groups']:
            next_group_players[ng['groupIndex']] = {p['name'] for p in ng['players']}

        # 对当前轮每个组，找它的前4名实际出现在下一轮哪个组
        for g in curr['groups']:
            top4 = {p['name'] for p in g['players'][:4]}
            best_group = None
            best_overlap = 0
            for ng_idx, ng_names in next_group_players.items():
                overlap = len(top4 & ng_names)
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_group = ng_idx

            if best_group is not None and best_group != g.get('nextRoundGroupId'):
                old = g.get('nextRoundGroupId')
                g['nextRoundGroupId'] = best_group
                changes.append(f"晋级 {curr['label']} {g['label']}: {old} → {best_group}")

    # ── 第三步：重命名标签 ──
    for r in rounds:
        groups = r['groups']
        is_final = (len(groups) == 1 and len(rounds) > 1)
        for i, g in enumerate(groups):
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
