export type Patient = { id: string; mrn: string; name: string; dob: string; sex: string; phone: string }
export type Report = { id: string | null; patientId: string; modality: string; body: string; status: string; updatedAt?: string }
export type Template = { id: string; name: string; modality: string; body: string }
export type Data = { patients: Patient[]; reports: Report[]; templates: Template[]; modalities: string[]; uploads?: unknown[] }
