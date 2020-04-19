LANGS=(nl)

pybabel extract -F babel.cfg -o boggle.pot -c "i18n" .
for lang in $LANGS 
do
    pybabel update -i boggle.pot -d translations -l $lang -D boggle
done
