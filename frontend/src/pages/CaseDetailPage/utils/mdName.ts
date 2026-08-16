// md 文件名变体匹配：PDF 与其对应 MD 的 _去水印 后缀可能不一致
// （PDF 去水印后带 _去水印 而 MD 沿用原名，或反之），
// 用 .replace(/\.pdf$/i, '.md') 单变体推导会失配，导致 ⚠️ 与修复入口静默缺失

/** 由 PDF 文件名推导所有可能的 md 文件名（原名 + 加/去 _去水印 变体） */
export function mdNameVariants(pdfName: string): string[] {
  const stem = pdfName.replace(/\.pdf$/i, '')
  const variants = [`${stem}.md`]
  if (stem.endsWith('_去水印')) {
    variants.push(`${stem.slice(0, -'_去水印'.length)}.md`)
  } else {
    variants.push(`${stem}_去水印.md`)
  }
  return variants
}

/** 判断 md 文件名是否可能是该 PDF 的转换产物（含 _去水印 变体） */
export function isMdFileOfPdf(mdFile: string, pdfName: string): boolean {
  return mdNameVariants(pdfName).includes(mdFile)
}
