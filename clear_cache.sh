#!/bin/bash
# Script para limpar cache Python e forçar reload

echo "🧹 Limpando cache Python..."

# Remover todos os arquivos .pyc
find . -type f -name "*.pyc" -delete
echo "✅ Arquivos .pyc removidos"

# Remover todos os diretórios __pycache__
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
echo "✅ Diretórios __pycache__ removidos"

# Remover bytecode em auto_sender
rm -f auto_sender.pyc 2>/dev/null
rm -rf __pycache__/auto_sender.*.pyc 2>/dev/null
echo "✅ Cache do auto_sender limpo"

echo "🎉 Cache limpo com sucesso!"
echo "🔄 Reinicie o serviço para aplicar as mudanças"
