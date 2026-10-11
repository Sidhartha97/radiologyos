import io
import re
import zipfile
import xml.etree.ElementTree as ET

MODALITIES = {
    'CT': r'\b(?:HRCT|CECT|NCCT|CT|computed tomography)\b',
    'MRI': r'\b(?:MRI|MR|MRCP|MRA|MRV|magnetic resonance)\b',
    'USG': r'\b(?:USG|ultrasound|ultrasonography|sonography)\b',
    'XRAY': r'\b(?:x[ -]?ray|radiograph(?:y|s)?|PA view|AP view)\b',
    'BMD': r'\b(?:BMD|DEXA|DXA|bone densitometry)\b',
    'MAMMOGRAM': r'\b(?:mammogram|mammography|mammo)\b',
}
REGIONS = {
    'BRAIN': r'\b(?:brain|intracranial|cerebral)\b',
    'CHEST': r'\b(?:chest|thorax|lung)\b',
    'ABDOMEN': r'\b(?:abdomen|abdominal|MRCP|liver|hepatomegaly|gallbladder)\b',
    'PELVIS': r'\b(?:pelvis|pelvic)\b',
    'SPINE': r'\b(?:spine|lumbar|lumbosacral|cervical spine|LS spine)\b',
    **{x: rf'\b{x}\b' for x in ('KNEE','SHOULDER','ELBOW','WRIST','HIP','ANKLE','FOOT','NECK','HEAD')},
    'WHOLE_BODY': r'\bwhole body\b',
}
SECTIONS = ('CLINICAL HISTORY','HISTORY','TECHNIQUE','COMPARISON','FINDINGS','OBSERVATIONS','IMPRESSION','CONCLUSION')


def extract(content, suffix):
    suffix = suffix.lower()
    if suffix == '.docx':
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            root = ET.fromstring(archive.read('word/document.xml'))
            numbering = ET.fromstring(archive.read('word/numbering.xml')) if 'word/numbering.xml' in archive.namelist() else None
        ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
        abstract = {}; numbers = {}; counts = {}; lines = []
        if numbering is not None:
            for node in numbering.findall(ns+'abstractNum'):
                formats={level.get(ns+'ilvl'):level.find(ns+'numFmt').get(ns+'val') for level in node.findall(ns+'lvl') if level.find(ns+'numFmt') is not None}
                abstract[node.get(ns+'abstractNumId')]=formats
            for node in numbering.findall(ns+'num'):
                pointer=node.find(ns+'abstractNumId')
                if pointer is not None: numbers[node.get(ns+'numId')]=abstract.get(pointer.get(ns+'val'),{})
        for paragraph in root.iter(ns+'p'):
            line=''.join(n.text or '' for n in paragraph.iter(ns+'t'))
            properties=paragraph.find(ns+'pPr/'+ns+'numPr')
            if properties is not None and line.strip():
                number=properties.find(ns+'numId'); level=properties.find(ns+'ilvl')
                identity=number.get(ns+'val') if number is not None else '0'
                depth=level.get(ns+'val') if level is not None else '0'
                fmt=numbers.get(identity,{}).get(depth,'bullet')
                counts[identity]=counts.get(identity,0)+1
                line=('• ' if fmt=='bullet' else str(counts[identity])+'. ')+line
            lines.append(line)
        text = '\n'.join(lines)
    elif suffix == '.pdf':
        from pypdf import PdfReader
        text = '\n\n'.join(page.extract_text() or '' for page in PdfReader(io.BytesIO(content)).pages)
    elif suffix == '.txt':
        text = content.decode('utf-8-sig')
    else:
        raise ValueError('Supported files: DOCX, PDF, TXT')
    if not text.strip():
        raise ValueError('No readable text; scanned PDFs require OCR before ingestion')
    return text.strip()


def normalize_modality(value):
    value = (value or '').upper().strip()
    return {'MR':'MRI', 'X-RAY':'XRAY', 'X RAY':'XRAY', 'ULTRASOUND':'USG'}.get(value, value)


def classify(text, filename='', fallback=None):
    # Prefer the filename and title over mentions of other modalities in findings.
    title = '\n'.join(text.splitlines()[:4])
    heading = filename.replace('_',' ') + '\n' + title
    detected = [m for m, pattern in MODALITIES.items() if re.search(pattern, heading, re.I)]
    if not detected:
        detected = [m for m, pattern in MODALITIES.items() if re.search(pattern, text, re.I)]
    modality = detected[0] if len(detected) == 1 else 'UNKNOWN'
    region = next((r for r, pattern in REGIONS.items() if re.search(pattern, heading, re.I)), None)
    region = region or next((r for r, pattern in REGIONS.items() if re.search(pattern, text, re.I)), 'UNKNOWN')
    study = next((line.strip() for line in text.splitlines()[:4]
                  if any(re.search(pattern, line, re.I) for pattern in MODALITIES.values())
                  and not re.search(r'\breport\b|patient|DEMO-', line, re.I)), '')
    result = {'modality': modality, 'body_region': region,
              'study_type': study[:128] or f'{modality} {region}',
              'classification_method': 'rule',
              'classification_confidence': 0.95 if modality != 'UNKNOWN' and region != 'UNKNOWN' else 0.4}
    if fallback and (modality == 'UNKNOWN' or region == 'UNKNOWN'):
        extra = fallback(heading + '\n' + text[:3000])
        if extra.get('modality') in {*MODALITIES, 'OTHER', 'UNKNOWN'} and extra.get('body_region') in {*REGIONS, 'OTHER', 'UNKNOWN'}:
            result.update(modality=extra['modality'],body_region=extra['body_region'],
                study_type=str(extra.get('study_type',result['study_type']))[:128],
                classification_confidence=max(0,min(1,float(extra.get('classification_confidence',0.5)))),classification_method='bedrock')
    result['study_key']=re.sub(r'\s+',' ',result['study_type']).strip().upper()
    return result


def validate_single_report(text):
    if sum(line.strip().strip('#*: ').upper() in ('FINDINGS','OBSERVATIONS') for line in text.splitlines()) > 1:
        raise ValueError('Multiple reports detected in one file. Split into one study per file before batch ingestion to avoid assigning one modality to different studies.')


def structure(text):
    found = []
    sections = {}
    current = None
    for line in text.splitlines():
        heading = line.strip().strip('#*: ').upper()
        if heading in SECTIONS:
            found.append(heading)
            current = heading
            sections.setdefault(current, [])
        elif current and line.strip():
            sections[current].append(line.strip())
    def style(name):
        lines = sections.get(name, [])
        if any(re.match(r'^\d+[.)]\s', x) for x in lines): return 'numbered'
        if any(re.match(r'^[•*-]\s', x) for x in lines): return 'bullet'
        return 'paragraph'
    return {'sections': ','.join(dict.fromkeys(found)), 'findings_style': style('FINDINGS'),
            'impression_style': style('IMPRESSION'), 'verbosity': 'concise' if len(text.split()) < 250 else 'detailed',
            'tone':'report_style', 'formatting_pattern':'sectioned' if found else 'free_text',
            'style_method':'rule'}
