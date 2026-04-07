#!/bin/bash
# 导入 mock 数据到 MongoDB
# 使用前确保 MongoDB 已启动，连接地址正确

DB="hearthstone"
DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== 导入 league mock 数据到数据库: $DB ==="

# 清空旧数据
echo "清空旧集合..."
mongosh "$DB" --eval "db.league_players.drop(); db.league_matches.drop(); db.league_active_games.drop();" --quiet

# 导入选手
echo "导入 league_players..."
mongoimport --db "$DB" --collection league_players --file "$DIR/league_players.json" --jsonArray

# 导入对局
echo "导入 league_matches..."
mongoimport --db "$DB" --collection league_matches --file "$DIR/league_matches.json" --jsonArray

# 导入活跃对局
echo "导入 league_active_games..."
mongoimport --db "$DB" --collection league_active_games --file "$DIR/league_active_games.json" --jsonArray

# 创建索引
echo "创建索引..."
mongosh "$DB" --eval '
  db.league_players.createIndex({ "battleTag": 1 }, { unique: true });
  db.league_players.createIndex({ "accountIdLo": 1 }, { unique: true });
  db.league_players.createIndex({ "totalPoints": -1 });
  db.league_matches.createIndex({ "gameUuid": 1 }, { unique: true });
  db.league_matches.createIndex({ "endedAt": -1 });
  db.league_active_games.createIndex({ "gameUuid": 1 }, { unique: true });
' --quiet

echo "=== 导入完成 ==="
echo ""
echo "验证数据："
mongosh "$DB" --eval '
  print("league_players: " + db.league_players.countDocuments() + " 条");
  print("league_matches: " + db.league_matches.countDocuments() + " 条");
  print("league_active_games: " + db.league_active_games.countDocuments() + " 条");
' --quiet
